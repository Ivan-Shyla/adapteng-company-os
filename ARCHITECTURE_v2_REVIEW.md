# ARCHITECTURE.md v2.0 — Review: ошибки, сомнения, вопросы

> Дата ревью: 2026-07-24. Ревьюер: Claude (по запросу владельца).
> Формат: A — явные ошибки/противоречия; B — что проверено и подтверждено;
> C — сомнения в подходе с предлагаемым решением; D — мелочи.
> Каждый пункт помечен уверенностью: high / medium / low.

---

## A. Явные ошибки и внутренние противоречия

### A1. €10/month AI cap несовместим с «code mode реализует весь backlog» — high

§7.2: «Этот режим [code_change] используется сразу для реализации всего backlog
Company OS». §7.4/§9.1: жёсткий cap €10/month на AI API.

Агентная разработка кода — самый токеноёмкий вид AI-работы: одна bounded-задача
с итерациями, тестами и review легко стоит $1–5; в backlog десятки задач
(COS/AUT/AG/MKT/N8N/AI). €10/month не покрывает и десятой части, либо
разработка идёт через личную подписку (Claude Pro/Max, Codex и т.п.) вне cap —
но тогда это нигде не сказано, а §8.2 запрещает только *free* consumer tiers.

**Правка:** явно разделить два бюджета: (1) `dev_model_budget` — разработка
через подписку owner'а или отдельный лимит, зафиксировать какой; (2)
`runtime_model_cap €10/month` — только business_artifact runtime. Иначе первый
же спринт либо пробьёт cap, либо создаст теневое использование вне учёта.

### A2. Конфликт записи Baserow: человек против idempotent upsert — high

§5.2 требует «создаёт/обновляет Baserow record… при retry reconciles». Но
Baserow — единственное место, где Иван *руками редактирует* те же записи
(next_action, stage, fit). Idempotent upsert без карты владения полей = при
повторном прогоне workflow молча затирает ручные правки (классический
last-write-wins). Ни §3, ни §5 не определяют, какие поля принадлежат workflow,
а какие — человеку.

**Правка:** добавить в §5.2 field-ownership правило per table:
workflow-owned (`source_ref`, `automation_run_id`, `drive_folder_id`,
projection-поля) обновляются upsert'ом всегда; human-owned (`stage`, `fit`,
`next_action*`, `outcome`) workflow заполняет ТОЛЬКО при создании записи, далее
patch запрещён. Это одна таблица в документе и одно правило в adapter
(`AUT-001` DoD дополнить: «human-owned поля не перезаписываются»).

### A3. «Аккаунт Полины owner/admin» против бюджета на одного user — high

§1.1 допускает owner/admin доступ для второго человека; §4.1/§9.1 бюджетируют
одного user Business Standard. В Google Workspace второй полноценный user =
вторая платная лицензия. При этом единственный super-admin аккаунт — это
bus-factor: потеря доступа Ивана = потеря Workspace.

**Правка:** оформить второй аккаунт как **бесплатный Cloud Identity Free**
super-admin без Workspace-лицензии (официально поддерживается): recovery-админ
без почты/Drive. Одна строка в §4.1 и в `COS-001` DoD («второй break-glass
admin через Cloud Identity Free, MFA, recovery codes offline»). Проверить
доступность Cloud Identity Free при checkout — уверенность в механике high,
в текущем UI-пути medium.

---

## B. Проверено сегодня и подтверждено (менять не надо)

- **Цены моделей §7.4 корректны** (проверено 2026-07-24 по официальным
  страницам): GPT-5.4 nano $0.20/$1.25 (cached $0.02), mini $0.75/$4.50;
  Sonnet 5 intro $2/$10 до 2026-08-31, потом $3/$15; Gemini 2.5 Flash-Lite
  $0.10/$0.40. Единственное дополнение: лестница моделей меняется ежемесячно
  (у OpenAI уже есть 5.6-семейство с кандидатом Luna $1/$6 в нише «дёшево и
  качественно») — прайс держать в config Gateway, а не в этом документе; в
  документе оставить только принцип benchmark'а.
- EU data residency у OpenAI тарифицируется с наценкой (например +10% для
  GPT-5.4 nano) — полезно знать заранее для пункта C4.
- Решение Baserow self-hosted Free: API, tokens со scope на таблицы и webhooks
  входят в core — для одного пользователя ограничений не видно (проверить
  фактом при `COS-003`, ожидание — всё ок).
- Схема approval (canonical Postgres ledger + read-only projection в Baserow +
  одноразовый token + hash snapshot в limited-access `04_Approved`) —
  архитектурно правильная; отдельно похвалю §8.3: «documents and emails are
  untrusted input» — это корректная защита от prompt injection.
- Порядок cutover (approval-workflows последними) и ручное пересоздание
  credentials на self-hosted — верно, из n8n Cloud credentials не экспортируются.

---

## C. Сомнения в подходе (не ошибки, но можно лучше)

### C1. Коммерческий вакуум первых 60 дней — high, самый важный пункт

В backlog до `10.7` (Days 60–90!) нет ни одного пункта, который создаёт контакт
с потенциальным клиентом или партнёром. Первые два месяца компания строит OS
для операций, которых нет (§1.4: leads = 0). Твой же kill-критерий §12 —
«produces activity but no client/action/outcome» — применим к самому плану.

**Правка:** перенести «initial partner/account list from known European
network» из 10.7 в Days 4–14 (он и так почти там: `COS-005` уже грузит
partners) и добавить `BIZ-001` (Days 8–21): «создать 10 outreach Actions из
личной partner network с due_at; DoD: 10 отправленных сообщений, outcome
записан». Это одновременно единственный настоящий UAT для Pipeline/Actions
views — тестировать интерфейс на синтетике хуже, чем на реальных контактах.
Стоимость правки: ноль инфраструктуры.

### C2. Content-first как первый AI skill — medium

Обоснование §7.5 честное (automation существует, drafts проверяемы, RFQ нет).
Принимаю как pilot артефакт-пайплайна. Но фиксирую tension: контент — самый
медленный канал к первому клиенту в industrial B2B; а Opportunity/Partner Radar
отложен «до target account list», создание которого само стоит в Days 60–90 —
циклическая задержка. Если C1 принят, список появляется к Day 14 и Radar может
стартовать ~Day 30–45 параллельно content-пилоту (Radar читает только public
sources — его admission-требования минимальны).

### C3. Ollama на том же Hetzner — medium-high

«Local Ollama where quality allows» для задач со 100% citation-gate: модели,
которые влезут в CPU-only VPS рядом с Postgres+Baserow+n8n, с высокой
вероятностью не пройдут citation-дисциплину, а benchmark съест время cap'а и
недели календаря. Плюс co-location риск: inference-нагрузка на том же хосте,
где живёт вся компания.

**Правка:** сузить в §7.4: Ollama тестируется только на classify/extract, не на
drafts; inference на company-хосте запрещён до отдельного решения по железу;
если classify дешевле $0.20/1M нанo-моделью API — Ollama просто не нужен на
этой стадии. Не дать «бесплатно» задержать пилот.

### C4. Персональные данные → внешняя модель — high (закрыть до Lead Triage)

Data policy §8.2 классифицирует документы, но не отвечает на конкретный вопрос:
Website Lead Triage (очередь §7.6 №2) читает `People` (имя, email, телефон) —
это personal data, уходящая внешнему провайдеру. GDPR-минимум:

1. правило в §8.2: контактные PII вырезаются/псевдонимизируются до model call
   (triage нужен текст запроса, не email отправителя);
2. privacy policy сайта: упомянуть автоматизированную обработку и список
   processors (hosting, model provider);
3. при необходимости — EU endpoints провайдера (с учётом наценки, см. B).

Это три маленьких пункта, но их отсутствие — реальный компликационный риск при
первом же корпоративном клиенте с due diligence опросником.

### C5. Разделение личных и company credentials — без ID и срока — medium

§6.3 констатирует: JM/EC делят OpenAI/Google/Telegram/Gmail credentials с
company-workflows и JM пишет в `adapteng_ops`. Правила заявлены, но в backlog
нет строки с датой. Утечка/бан общего ключа личным workflow кладёт company
automation.

**Правка:** добавить `SEC-002` (Days 4–14): отдельные API keys и budget для
JM/EC, отдельная Postgres schema/store для JM; DoD: ни один личный workflow не
использует company credential. §8.1 уже требует «до подключения новых
write-capable automations» — просто сделать это исполняемой строкой.

### C6. Baserow Actions против Postgres task queue — вопрос на подтверждение — medium

Machine tasks живут в Postgres, человеческие Actions — в Baserow. Где живёт
гибрид «Ivan review draft X» (создан workflow, исполняется человеком)?
Предлагаемый ответ: Baserow Action (human-facing) + link на Postgres run;
правило «каждый pending review = ровно одна Baserow Action» — иначе появятся
два конкурирующих списка задач. Подтверди и зафиксируй одной строкой в §3.4.

---

## D. Мелочи

- **D1.** §8.4 Shared Drive backup «version history + export»: у Google нет
  штатного расписания полного экспорта. Дешёвое решение: rclone/API-экспорт
  `10_Company` + `04_Approved` на существующий Hetzner storage раз в неделю.
  Одна строка в `OPS`-backlog.
- **D2.** SMTP для n8n-алертов и будущих email drafts нигде не назван (Zoho?).
  Зафиксировать в `Systems_Automations` как отдельную запись с owner.
- **D3.** DoD §13 п.10 («новый человек понимает систему за час») не имеет
  носителя: добавить `README.md` в `adapteng-company-os` как onboarding-вход.
- **D4.** `Opportunities.deadline` optional, но для type=RFQ/tender дедлайн —
  главное поле: сделать required для этих типов.
- **D5.** §12 запрещает ARCHITECTURE_v3 — правильно; предлагаю это ревью
  оформить как PR-issue-список к текущему файлу, а не как приложение-документ.

---

## Итоговая рекомендация

Документ готов к реализации после трёх правок из A (все — по одному абзацу) и
решения по C1 (перенос commercial-действий в первые две недели). C4 закрыть до
запуска Lead Triage, C5 — до первого нового write-capable workflow. Остальное —
по ходу. Ничто из найденного не требует менять выбранные системы (Baserow,
Workspace, Zoho MX, control-plane, cutover-план) — выбор обоснован и
подтверждается источниками.
