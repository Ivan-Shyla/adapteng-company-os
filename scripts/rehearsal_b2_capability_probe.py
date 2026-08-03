#!/usr/bin/env python3
"""Name what Backblaze B2 will actually serve, before pgBackRest asks it to.

pgBackRest reports a repository refusal as a bare ``HTTP request failed with
403`` and prints the request headers but never the response body, so the reason
B2 gave is discarded at exactly the moment it is needed. On 2026-08-03 that cost
six dispatches and an owner action item pointing at the wrong console page: the
403 came from an exhausted Backblaze daily cap, not from a defective key, and
the surrounding ``list-objects-v2`` calls kept passing because they are a
different transaction class.

So this probe asks the same object store the same questions through the AWS CLI,
which does print the body, and separates the three S3 transaction classes B2
meters independently:

    Class A  put-object, delete-object
    Class B  get-object, head-object      <- what stanza-create needs first
    Class C  list-objects-v2

A daily cap is enforced per class, so Class A and Class C can be perfectly
healthy while every Class B read is refused with ``403 AccessDenied``. A check
that only lists is therefore not evidence that a backup can be read back, and
that asymmetry is the whole reason this file exists.

The Class B probe reads an object this probe has just written and just seen in a
listing, so a refusal cannot be an absence artifact - the object is known to be
there. The missing-key probe reproduces the exact request pgBackRest makes
first, ``HEAD <repo>/archive/<stanza>/archive.info``, whose healthy answer is
404 and not 403.

No credential is read, printed or written here; the AWS CLI takes them from the
environment. Only the bucket, the prefix and B2's own error text are reported.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


# B2 answers an exhausted daily cap with the same 403 AccessDenied it uses for a
# permission refusal, and only the message distinguishes them. Ordering matters:
# matching AccessDenied first is precisely the misreading this probe exists to
# prevent, so the cap phrases are tested before the generic ones.
CAP_PHRASES = ("cap exceeded", "caps & alerts", "caps and alerts")
CREDENTIAL_PHRASES = (
    "signaturedoesnotmatch",
    "invalidaccesskeyid",
    "invalidaccesskey",
    "the request signature we calculated does not match",
)
NOT_FOUND_PHRASES = ("nosuchkey", "(404)", "not found", "nosuchbucket")
DENIED_PHRASES = ("accessdenied", "(403)", "forbidden", "unauthorized")

CAP_EXCEEDED = "cap_exceeded"
BAD_CREDENTIALS = "bad_credentials"
NOT_FOUND = "not_found"
ACCESS_DENIED = "access_denied"
UNKNOWN = "unknown"
SUCCEEDED = "succeeded"

VERDICT_OK = "ok"

# Which refusal to believe when several arrive at once. A cap message and a
# signature message name a cause; a bare 403 names only a status. HTTP forbids a
# response body on HEAD, so head-object can never say more than "Forbidden" and
# is the weakest witness available even when it is the first to answer. Reading
# the weakest witness first is the exact misreading this probe exists to
# prevent, so the strongest is selected deliberately rather than by arrival.
REASON_PRECEDENCE = (
    CAP_EXCEEDED,
    BAD_CREDENTIALS,
    ACCESS_DENIED,
    NOT_FOUND,
    UNKNOWN,
)
SELF_DESCRIBING = (CAP_EXCEEDED, BAD_CREDENTIALS)
BODYLESS_OPERATIONS = ("head-object",)


def classify(returncode: int, stderr: str) -> str:
    """Map one AWS CLI outcome onto the reason B2 actually gave.

    Pure, and deliberately so: the classification is the part that was got wrong
    by hand, so it is unit tested rather than only exercised when B2 happens to
    be refusing something.
    """
    if returncode == 0:
        return SUCCEEDED
    haystack = stderr.lower()
    for phrases, reason in (
        (CAP_PHRASES, CAP_EXCEEDED),
        (CREDENTIAL_PHRASES, BAD_CREDENTIALS),
        (NOT_FOUND_PHRASES, NOT_FOUND),
        (DENIED_PHRASES, ACCESS_DENIED),
    ):
        if any(phrase in haystack for phrase in phrases):
            return reason
    return UNKNOWN


@dataclass
class Probe:
    name: str
    transaction_class: str
    operation: str
    reason: str
    returncode: int
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.reason == SUCCEEDED


@dataclass
class Report:
    bucket: str
    endpoint: str
    prefix: str
    missing_key: str
    verdict: str = VERDICT_OK
    reason: str = SUCCEEDED
    detail: str = ""
    probes: list[Probe] = field(default_factory=list)


def first_message_line(stderr: str) -> str:
    """B2's sentence, without the AWS CLI's traceback or usage noise."""
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


class Aws:
    def __init__(self, endpoint: str, bucket: str) -> None:
        self.endpoint = endpoint
        self.bucket = bucket

    def run(self, operation: str, *arguments: str) -> tuple[int, str, str]:
        # The endpoint and bucket go directly after the operation so that a
        # positional argument, such as get-object's output file, stays last
        # where the AWS CLI expects it.
        completed = subprocess.run(
            [
                "aws",
                "s3api",
                operation,
                "--endpoint-url",
                self.endpoint,
                "--bucket",
                self.bucket,
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def probe(
        self, name: str, transaction_class: str, operation: str, *arguments: str
    ) -> Probe:
        returncode, _, stderr = self.run(operation, *arguments)
        reason = classify(returncode, stderr)
        return Probe(
            name=name,
            transaction_class=transaction_class,
            operation=operation,
            reason=reason,
            returncode=returncode,
            message=first_message_line(stderr),
        )


def annotate(title: str, body: str) -> None:
    # GitHub renders a single line, so the body is flattened rather than
    # silently truncated at the first newline.
    flattened = " ".join(body.split())
    print(f"::error title={title}::{flattened}")


def strongest_refusal(probes: "list[Probe]") -> "Probe | None":
    """The refusal that explains the most, not the one that answered first."""
    refusals = [probe for probe in probes if not probe.succeeded]
    if not refusals:
        return None

    def rank(probe: Probe) -> tuple[int, int]:
        try:
            reason = REASON_PRECEDENCE.index(probe.reason)
        except ValueError:
            reason = len(REASON_PRECEDENCE)
        return (reason, 1 if probe.operation in BODYLESS_OPERATIONS else 0)

    return min(refusals, key=rank)


def fail(report: Report, verdict: str, probe: Probe, title: str, body: str) -> Report:
    report.verdict = verdict
    report.reason = probe.reason
    report.detail = probe.message
    annotate(title, f"{body} B2 said: {probe.message or '(no message)'}")
    return report


CAP_EXPLANATION = (
    "Backblaze meters put and delete (Class A), get and head (Class B) and list"
    " (Class C) against separate daily caps, and refuses an exhausted one with"
    " the same 403 AccessDenied it uses for a permission problem. Class A writes"
    " and Class C listings still succeed here, which is why any check built only"
    " from listings looks healthy. pgBackRest needs a Class B read for the very"
    " first repository call it makes, so no stanza-create, backup, verify or"
    " restore can run until the cap resets at 00:00 GMT or the owner raises it on"
    " the Backblaze Caps & Alerts page. This is a billing setting, not a bucket"
    " permission and not a defective key."
)

CREDENTIAL_EXPLANATION = (
    "B2 rejected the signature or the key id itself, so this is a credential"
    " problem rather than a scope, metering or request-shaping one."
)


def fail_on_self_describing(report: Report, probe: Probe) -> "Report | None":
    """Honour a refusal that states its own cause, whichever key provoked it.

    A cap message and a signature message are about the account and the
    credentials, not about the object asked for, so they are conclusive wherever
    they appear and do not need corroborating by a second probe.
    """
    if probe.reason == CAP_EXCEEDED:
        return fail(
            report,
            "class_b_read_refused_cap_exceeded",
            probe,
            "Backblaze B2 daily cap is exhausted",
            CAP_EXPLANATION,
        )
    if probe.reason == BAD_CREDENTIALS:
        return fail(
            report,
            "credentials_rejected",
            probe,
            "Backblaze B2 rejected the credentials",
            CREDENTIAL_EXPLANATION,
        )
    return None


def run_probe(
    bucket: str, endpoint: str, prefix: str, stanza: str, scope: str
) -> Report:
    normalised = prefix.strip("/")
    if not normalised:
        raise SystemExit("the repository prefix must not be empty or '/'")

    missing_key = f"{normalised}/archive/{stanza}/archive.info"
    probe_key = f"{normalised}/capability-probe/{scope}.txt"
    # Structural, not stylistic: every object this probe touches is one it
    # constructed under the prefix it was given, and the only delete it issues
    # names a single key it has just created. There is no recursive delete here.
    if not probe_key.startswith(f"{normalised}/"):
        raise SystemExit("refusing to write outside the repository prefix")

    report = Report(
        bucket=bucket, endpoint=endpoint, prefix=normalised, missing_key=missing_key
    )
    aws = Aws(endpoint=endpoint, bucket=bucket)

    if shutil.which("aws") is None:
        report.verdict = "aws_cli_missing"
        report.reason = UNKNOWN
        annotate(
            "AWS CLI is not installed",
            "The capability probe needs the AWS CLI to read B2's error body.",
        )
        return report

    # Class C first. It is the cheapest call and the one that keeps passing
    # while Class B is capped, so establishing that it works is what makes the
    # later Class B refusal meaningful rather than ambiguous.
    listing = aws.probe(
        "class_c_list_repository_prefix",
        "C",
        "list-objects-v2",
        "--prefix",
        f"{normalised}/",
        "--max-items",
        "1",
    )
    report.probes.append(listing)
    if not listing.succeeded:
        return fail(
            report,
            "class_c_list_refused",
            listing,
            "Backblaze B2 refuses Class C listing",
            "The repository prefix cannot even be listed, so the credentials or"
            " their scope are wrong before any backup question arises.",
        )

    # The exact request pgBackRest issues first. 404 is the healthy answer; a
    # 403 here is the failure the rehearsal actually hits.
    missing_head = aws.probe(
        "missing_key_head_object", "B", "head-object", "--key", missing_key
    )
    report.probes.append(missing_head)
    missing_get = aws.probe(
        "missing_key_get_object",
        "B",
        "get-object",
        "--key",
        missing_key,
        os.devnull,
    )
    # HEAD carries no response body by definition, so the same key is asked for
    # with GET purely to make B2 state its reason in words.
    report.probes.append(missing_get)

    # A cap or a signature refusal describes the account, not the key, so it is
    # answered here rather than after a Class A write that would only confirm
    # what has already been said in plain words.
    conclusive = strongest_refusal([missing_head, missing_get])
    if conclusive is not None and conclusive.reason in SELF_DESCRIBING:
        settled = fail_on_self_describing(report, conclusive)
        if settled is not None:
            return settled

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        sent = directory / "probe-sent.txt"
        received = directory / "probe-received.txt"
        sent.write_text(f"adapteng b2 capability probe scope={scope}\n", encoding="utf-8")

        written = aws.probe(
            "class_a_put_object", "A", "put-object", "--key", probe_key, "--body", str(sent)
        )
        report.probes.append(written)
        if not written.succeeded:
            return fail(
                report,
                "class_a_write_refused",
                written,
                "Backblaze B2 refuses Class A writes",
                "The rehearsal cannot write its repository.",
            )

        try:
            present = aws.probe(
                "class_b_head_present_object", "B", "head-object", "--key", probe_key
            )
            report.probes.append(present)
            fetched = aws.probe(
                "class_b_get_present_object",
                "B",
                "get-object",
                "--key",
                probe_key,
                str(received),
            )
            report.probes.append(fetched)
        finally:
            removed = aws.probe(
                "class_a_delete_object", "A", "delete-object", "--key", probe_key
            )
            report.probes.append(removed)

        # Adjudicated over both reads together. Taking the first refusal would
        # take head-object's contentless "Forbidden" over get-object's sentence
        # naming the cap, which is the misreading in miniature.
        refused = strongest_refusal([present, fetched])
        if refused is not None:
            settled = fail_on_self_describing(report, refused)
            if settled is not None:
                return settled
            return fail(
                report,
                "class_b_read_refused",
                refused,
                "Backblaze B2 refuses Class B reads",
                "An object this probe had just written, and just listed, cannot"
                " be read back, so this is not a missing object. Class A writes"
                " and Class C listings succeed, so this key can write the"
                " repository but not read it.",
            )

        if received.read_bytes() != sent.read_bytes():
            report.verdict = "read_back_mismatch"
            report.reason = UNKNOWN
            annotate(
                "Backblaze B2 returned the wrong bytes",
                "The probe object read back does not match what was written.",
            )
            return report

        if not removed.succeeded:
            return fail(
                report,
                "class_a_delete_refused",
                removed,
                "Backblaze B2 refuses Class A deletes",
                "The rehearsal could not remove its own probe object, so it"
                " cannot clean up after itself either.",
            )

    # Deliberately confirmed with a listing rather than head-object: cleanup
    # verification must not itself depend on the transaction class that is at
    # issue, or a capped account would report litter it cannot see.
    returncode, listing_output, stderr = aws.run(
        "list-objects-v2", "--prefix", probe_key, "--max-items", "1"
    )
    absent = Probe(
        name="class_c_confirm_probe_absent",
        transaction_class="C",
        operation="list-objects-v2",
        reason=classify(returncode, stderr),
        returncode=returncode,
        message=first_message_line(stderr),
    )
    report.probes.append(absent)
    if absent.succeeded and probe_key in listing_output:
        report.verdict = "probe_object_survived_delete"
        report.reason = UNKNOWN
        annotate(
            "The probe object survived its own delete",
            "Delete reported success but the object is still listed, so this"
            " bucket does not really delete and retention cannot be enforced.",
        )
        return report

    # pgBackRest issues HEAD, so a refusal there blocks it even if GET answers
    # cleanly; either probe refusing is reported, and whichever of them explains
    # itself best is the one quoted.
    unhealthy = [
        probe
        for probe in (missing_head, missing_get)
        if probe.reason not in (NOT_FOUND, SUCCEEDED)
    ]
    offender = strongest_refusal(unhealthy)
    if offender is not None:
        return fail(
            report,
            "missing_key_probe_refused",
            offender,
            "Backblaze B2 refuses the key pgBackRest probes first",
            "Class B reads work on an object that exists, but the absent"
            f" {missing_key} answers with a refusal instead of 404, so this key"
            " cannot probe for a key that is not there yet - which is exactly"
            " what stanza-create does before it creates anything.",
        )

    return report


def render(report: Report) -> None:
    print(f"bucket prefix: {report.prefix}/")
    print(f"{'probe':<34} {'class':<6} {'rc':>3}  reason")
    for probe in report.probes:
        print(
            f"{probe.name:<34} {probe.transaction_class:<6} "
            f"{probe.returncode:>3}  {probe.reason}"
            + (f" - {probe.message}" if probe.message else "")
        )
    print(f"probe-verdict: {report.verdict}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--endpoint", required=True, help="host name, without a scheme")
    parser.add_argument("--prefix", required=True, help="the pgBackRest repository path")
    parser.add_argument("--stanza", required=True)
    parser.add_argument("--scope", required=True, help="run-scoped token for the probe key")
    parser.add_argument("--output", help="write the report as JSON to this path")
    arguments = parser.parse_args(argv)

    endpoint = arguments.endpoint
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"

    report = run_probe(
        bucket=arguments.bucket,
        endpoint=endpoint,
        prefix=arguments.prefix,
        stanza=arguments.stanza,
        scope=arguments.scope,
    )
    render(report)

    if arguments.output:
        path = Path(arguments.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")

    if report.verdict == VERDICT_OK:
        print("Backblaze B2 serves Class A writes, Class B reads and Class C listings.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
