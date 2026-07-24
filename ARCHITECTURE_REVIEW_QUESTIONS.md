# Вопросы и уточнения к архитектуре AdaptEng Company OS

> **Назначение документа:** независимая проверка файла `ARCHITECTURE.md` перед внесением изменений.  
> **Важно:** не изменять архитектуру автоматически на основании этих замечаний. Сначала проверить каждый вопрос по фактическим возможностям используемых систем, текущему коду, репозиториям, тарифам и уже реализованным workflow.
>
> Для каждого пункта необходимо дать:
>
> 1. **Статус:** подтверждено / частично подтверждено / не подтверждено / неактуально.
> 2. **Фактическое основание:** документация продукта, текущий код, конфигурация, схема БД или существующий workflow.
> 3. **Риск:** что произойдёт, если оставить архитектуру без изменений.
> 4. **Решение:** оставить как есть либо предложить точечное изменение.
> 5. **Влияние:** какие разделы архитектуры, таблицы, workflows и репозитории затрагиваются.
>
> Не следует расширять scope проекта без необходимости. Цель проверки — устранить реальные противоречия и риски, а не усложнить систему.

---

## 1. Границы хранения данных

### 1.1 GitHub, contracts и evidence

В начале архитектуры указано:

```text
GitHub = code, contracts и architecture
```

В других разделах contracts, client documents и evidence относятся к Google Shared Drive.

Необходимо проверить:

- Что именно подразумевается под `contracts` в зоне ответственности GitHub?
- Речь идёт о шаблонах, схемах и contracts/API contracts или о подписанных юридических договорах?
- Может ли формулировка ошибочно привести к хранению клиентских договоров, коммерческих документов или технических evidence в GitHub?
- Следует ли разделить:
  - API/data contracts и schemas — GitHub;
  - договоры, КП, клиентские документы и технические evidence — Shared Drive;
  - CI, deployment и restore evidence — GitHub или Postgres?
- Есть ли уже такие файлы в репозиториях, которые необходимо переклассифицировать?

Не менять формулировку до проверки фактического содержимого репозиториев.

---

## 2. Реализация views в Baserow

### 2.1 View `00 — Today`

Архитектура предполагает единый интерфейс `00 — Today`, в котором отображаются overdue и задачи на следующие семь дней.

Необходимо проверить:

- Может ли одна Baserow view объединять записи из `Organizations`, `Opportunities`, `Projects_Cases`, `Content_Items` и `Systems_Automations`?
- Или view всегда относится только к одной таблице?
- Должна ли таблица `Actions` стать единственным источником данных для `00 — Today`?
- Все ли решения, follow-up, проверки, review и исправления уже могут быть представлены через `Actions`?
- Не потеряется ли при этом важный контекст из связанных сущностей?
- Какой минимальный набор relation/lookup-полей нужен в `Actions`, чтобы view была действительно полезной?

### 2.2 View `01 — Ivan Decision`

Необходимо проверить:

- Может ли `01 — Ivan Decision` быть отдельной view таблицы `Actions` с типом действия `decide`?
- Или для content approval, opportunity decision и system decision нужны разные механизмы?
- Как избежать ситуации, когда решение требуется, но соответствующая Action не создана?
- Нужна ли автоматическая генерация Actions из других таблиц при переходе статуса?

---

## 3. Дублирование `next_action`

Поля `next_action` и `next_action_at` присутствуют одновременно в нескольких таблицах и в отдельной таблице `Actions`.

Необходимо проверить:

- Какой объект является фактическим source of truth для следующего действия?
- Предполагается ли ручное редактирование `next_action` в каждой бизнес-таблице?
- Каким образом предотвращается расхождение между:
  - `Opportunities.next_action`;
  - `Projects_Cases.next_action`;
  - `Systems_Automations.next_action`;
  - открытой записью в `Actions`?
- Нужны ли эти поля в бизнес-таблицах как projection/lookup, а не как редактируемые значения?
- Поддерживает ли Baserow необходимые lookup/rollup/formula-механизмы в выбранной версии?
- Может ли adapter или n8n безопасно поддерживать такую projection без циклических обновлений?
- Есть ли практическая польза от хранения нескольких будущих Actions, а не только одной `next_action`?

До ответа не удалять поля и не менять схему.

---

## 4. Реальное enforcement правил в Baserow

В таблицах используются формулировки `required`, `immutable`, `read-only`, а также условные правила: например, `loss_reason` обязателен только при `lost`.

Необходимо проверить:

- Какие из этих ограничений Baserow Free реально обеспечивает на уровне данных?
- Какие ограничения работают только в Baserow forms, но не при API-записи или ручном редактировании grid?
- Может ли пользователь вручную изменить поле, заявленное как `read-only projection`?
- Где фактически должны проверяться:
  - обязательность полей;
  - допустимые переходы статусов;
  - неизменяемость ID;
  - наличие `loss_reason`;
  - наличие `published_url`;
  - запрет ручного approval?
- Должен ли enforcement находиться в:
  - n8n adapter;
  - отдельном API/service layer;
  - Postgres constraints;
  - периодическом integrity workflow?
- Нужно ли явно указать в архитектуре различие между:
  - UI guidance;
  - business validation;
  - security boundary?

### 4.1 Integrity check

Проверить необходимость отдельного workflow, который выявляет:

- active entity без открытого Action;
- lost opportunity без `loss_reason`;
- published content без `published_url`;
- evidence без hash;
- повторяющиеся stable IDs;
- отсутствующие Drive files;
- ручное изменение approval projection;
- записи без owner;
- некорректные переходы статусов.

Следует определить, будет ли такой workflow preventive, scheduled или reconciliation-only.

---

## 5. Модель идентификаторов

Сейчас предполагаются ID вида `AE-ORG-0001`, `AE-OPP-0001` и т. д.

Необходимо проверить:

- Являются ли последовательные display IDs достаточно надёжными как canonical machine identifiers?
- Что произойдёт при:
  - параллельном создании записей;
  - повторном импорте;
  - восстановлении из backup;
  - миграции из Baserow;
  - создании записи offline или другим adapter?
- Нужен ли отдельный неизменяемый UUID?
- Следует ли разделить:
  - `entity_uuid` — canonical machine ID;
  - `organization_id` / `opportunity_id` — human-readable display code?
- Где должен генерироваться display code: Baserow, Postgres, adapter или n8n?
- Может ли изменение этой модели сейчас существенно усложнить систему без реальной пользы на первом этапе?

Изменение вводить только после оценки сложности и миграционных последствий.

---

## 6. Связи таблицы `Actions`

Сейчас используется конструкция:

```text
linked_entity_type/id
```

Необходимо проверить:

- Это два текстовых поля или polymorphic relation, реализованный adapter?
- Обеспечивается ли referential integrity?
- Можно ли удобно фильтровать Actions по Organization, Opportunity, Work, Content и System?
- Как обрабатывается удаление или архивирование связанной сущности?
- Не лучше ли на текущем масштабе использовать отдельные nullable relation fields:
  - `organization`;
  - `opportunity`;
  - `work`;
  - `content`;
  - `system`;
  - `document`?
- Допускается ли связь одной Action сразу с несколькими объектами?
- Нужен ли универсальный Entity Registry или это будет преждевременным усложнением?

---

## 7. Роли организаций

Поле `relationship_type` сейчас содержит одно значение:

```text
prospect / client / partner / OEM / supplier / other
```

Необходимо проверить:

- Может ли одна организация одновременно быть OEM, partner, supplier, integrator и prospect?
- Нужно ли заменить single select на multiple select `organization_roles`?
- Или требуется отдельная таблица Relationships, если отношения зависят от legal entity, проекта или периода?
- Достаточно ли оставить один общий `relationship_status`?
- Как будет отражаться ситуация, когда организация является партнёром в одном проекте и потенциальным клиентом в другом?

Не создавать отдельную relationship-модель без подтверждённой необходимости.

---

## 8. Stages и status Opportunities

Текущие stages:

```text
new / qualifying / decision / active / won / lost / parked
```

Необходимо проверить:

- Что конкретно означает `decision`?
- Что означает `active` и чем оно отличается от `qualifying`?
- Достаточно ли текущих stages для RFQ, tender, partner approach и service lead?
- Следует ли использовать более однозначную последовательность:

```text
new → qualifying → proposal → negotiation → won/lost/parked
```

- Нужен ли отдельный lifecycle `status = open / closed`, независимый от stage?
- Как обрабатывается opportunity, которая выиграна, но ещё не конвертирована в Project/Case?
- Какой stage должен создавать обязательную Action?
- Нужно ли различать commercial pipeline и partner relationship pipeline?

---

## 9. Source of truth на уровне данных

Архитектура определяет зоны ответственности систем, но не всегда определяет владельца конкретного поля или статуса.

Необходимо проверить и формально зафиксировать владельца для следующих данных:

| Данные | Предполагаемый владелец — проверить |
|---|---|
| Business relationship state | Baserow |
| Opportunity stage | Baserow |
| Open Actions и owner decisions | Baserow |
| Workflow runs, dedup и outbox | Postgres |
| Approval decision | Postgres |
| Approval projection | Baserow |
| Document binary и Drive version | Google Drive |
| Approved artifact hash | Postgres |
| Content publication state | WordPress или Baserow |
| Published URL | WordPress → Baserow projection |
| System configuration | GitHub |
| Secrets | n8n/Coolify/secret store |
| Cost ledger | Postgres |
| Model/provider configuration | GitHub или Postgres |

Вопросы:

- Кто имеет право изменять каждое значение?
- Какие данные являются canonical, а какие projection/cache?
- Что происходит при расхождении?
- Какая система побеждает при reconciliation?
- Какие изменения разрешены человеку напрямую, а какие только adapter?

---

## 10. Reconciliation между системами

Необходимо проверить необходимость отдельного reconciliation workflow:

```text
Baserow ↔ Postgres ↔ Drive ↔ WordPress
```

Он потенциально должен выявлять:

- Baserow record без Drive folder;
- Drive folder без Baserow record;
- `published` без реального WordPress URL;
- WordPress draft без Content_Item;
- approved hash, не совпадающий с текущим файлом;
- orphaned Postgres runs;
- stale approval projection;
- удалённый или перемещённый Drive file;
- Cloud/self-hosted workflow drift.

Нужно определить:

- частоту запуска;
- является ли он read-only;
- какие исправления допустимы автоматически;
- какие отклонения должны создавать Action;
- где хранится reconciliation report;
- входит ли это в pilot или должно быть добавлено позже.

---

## 11. Формат `source_ref` и `source_refs`

В нескольких таблицах `source_ref` или `source_refs` обязательны, но их формат не определён.

Необходимо проверить:

- Это URL, Drive ID, document ID, email ID, manifest ID или произвольная строка?
- Как один content item связывается с несколькими источниками?
- Как фиксируются:
  - дата получения;
  - hash;
  - classification;
  - licence;
  - original location;
  - retrieved snapshot?
- Достаточно ли JSON source manifest в Postgres/Drive?
- Или нужна отдельная таблица `Sources`?
- Не создаст ли девятая таблица лишнюю сложность на первом этапе?
- Можно ли сначала стандартизировать URI-схемы:

```text
drive://
email://
web://
github://
baserow://
```

и перейти к таблице Sources только после появления реального объёма?

### 11.1 Возможная таблица Sources — оценить, но не создавать автоматически

```text
source_id
source_type
original_url
drive_file_id
retrieved_at
sha256
classification
licence_status
created_by
```

Необходимо определить, какие use cases действительно требуют отдельной сущности.

---

## 12. Версионирование документов и evidence

Сейчас `Documents_Evidence` содержит `document_id`, `version`, `status` и Drive pointer.

Необходимо проверить:

- Обновляется ли одна Baserow record при создании новой версии?
- Или каждая версия должна быть отдельной записью?
- Как сохраняется история superseded/obsolete versions?
- Что именно является immutable после approval?
- Может ли Google Drive version history считаться достаточной системой версий?
- Нужны ли отдельные идентификаторы:

```text
document_series_id
document_version_id
version_number
supersedes_version_id
```

- Как обрабатывается новая версия уже issued документа?
- Где находится canonical hash каждой версии?
- Нужно ли хранить отдельный snapshot/export или достаточно Drive native format?

Не усложнять модель, если Drive version history и Postgres approval ledger уже закрывают требования.

---

## 13. GDPR и контакты

Сейчас в `People` есть:

```text
consent_basis = business contact / form consent / unknown
```

Необходимо проверить:

- Является ли `business contact` юридическим основанием обработки или только источником контакта?
- Следует ли разделить:
  - источник контакта;
  - lawful basis;
  - marketing permission;
  - objection/do-not-contact status?
- Какие типы контактов планируется хранить:
  - входящие заявки;
  - личные профессиональные контакты Ивана;
  - public business contacts;
  - outbound prospects;
  - partner network?
- Требуется ли документировать legitimate interest assessment?
- Как фиксируется privacy notice version для website forms?
- Как обрабатывается возражение против direct marketing?
- Какой retention period нужен для lost/parked leads?
- Нужны ли поля:

```text
lawful_basis
contact_source
marketing_permission
privacy_notice_version
collected_at
do_not_contact
objection_at
retention_review_at
```

- Какие из них действительно нужны в первые 90 дней, а какие можно добавить позже?

Юридические поля не добавлять без проверки реального процесса и применимого законодательства.

---

## 14. Доступ пользователей в Baserow Free

Архитектура предполагает, что Иван работает в системе, а Полина может иметь owner/admin access.

Необходимо проверить:

- Какие permissions реально доступны в Baserow self-hosted Free?
- Получает ли участник workspace доступ ко всем таблицам?
- Можно ли ограничить доступ Полины только recovery/admin-функцией без доступа к business data?
- Можно ли выдавать table-scoped API tokens automation и agent adapters?
- Как разделяются:
  - interactive user access;
  - automation token;
  - agent token;
  - backup/recovery access?
- Достаточно ли на старте одного interactive user?
- Какой фактический trigger перехода на paid RBAC или другую систему?
- Нужно ли прямо зафиксировать, что до перехода на RBAC новые пользователи не добавляются в Company Operations?

---

## 15. Google Workspace и break-glass access

Необходимо проверить:

- Может ли организация безопасно работать с одним оплачиваемым Workspace user?
- Нужен ли второй super-admin или break-glass account?
- Возможно ли использовать Cloud Identity Free или другой вариант без второй полной лицензии?
- Какой account является:
  - ежедневным рабочим;
  - super-admin;
  - recovery/break-glass;
  - service account?
- Где хранятся recovery codes?
- Как проверяется доступ при потере основного аккаунта?
- Не создаёт ли второй admin лишнюю поверхность атаки?
- Следует ли включить hardware security keys для super-admin?

Изменение тарифов или создание дополнительных аккаунтов делать только после проверки актуальной модели Google Workspace.

---

## 16. Service account и Shared Drive permissions

Архитектура предполагает draft service account и отдельный approval-adapter service account.

Необходимо проверить:

- Может ли Google service account быть участником Shared Drive в выбранной конфигурации Workspace?
- Какие роли доступны service account?
- Можно ли ограничить draft account так, чтобы он:
  - создавал source/draft folders and files;
  - не изменял approved artifacts;
  - не мог перемещать или удалять критические folders?
- Работает ли limited-access folder внутри Shared Drive именно так, как предполагается?
- Может ли approval account создавать snapshot в `04_Approved`, не получая избыточных прав на весь Drive?
- Не проще ли реализовать approval snapshot через один контролируемый adapter с короткоживущей credential?
- Как проверяется невозможность обхода approval через ручной n8n workflow?

---

## 17. Backup n8n и Baserow

В архитектуре указаны export и restore evidence, но состав backup необходимо уточнить.

Проверить, что входит в восстановление n8n:

- database;
- encryption key;
- credentials;
- workflows;
- environment variables;
- local binary-data volume;
- community nodes;
- execution data;
- Coolify deployment configuration;
- reverse proxy/DNS configuration.

Проверить, что входит в восстановление Baserow:

- database;
- media/uploads;
- environment/config;
- secrets;
- container volumes;
- version compatibility;
- API tokens;
- user accounts.

Дополнительные вопросы:

- Находится ли backup физически вне того же Hetzner server?
- Использует ли он отдельные credentials?
- Защищён ли backup от удаления после компрометации основного сервера?
- Проверяется ли restore на чистом окружении?
- Как документируется RPO/RTO evidence?
- Достаточно ли monthly restore для Postgres/Baserow?
- Нужно ли добавить backup до Baserow deployment в обязательные prerequisites?

---

## 18. Разделение company и personal automations

Сейчас MM, JM и EC используют общий n8n Cloud runtime и часть общих credentials.

Необходимо проверить:

- Какие credentials сейчас действительно общие?
- Пишет ли Job Monitor в company `adapteng_ops`?
- Есть ли доступ JM/EC к company Google account, Gmail, Telegram bot или AI budget?
- Достаточно ли отдельных schemas и API keys?
- Нужны ли:
  - отдельные Postgres roles;
  - отдельные databases;
  - отдельные n8n projects/instances;
  - отдельные Telegram bots;
  - отдельные model budgets;
  - отдельные encryption boundaries?
- Что является минимальным безопасным разделением на текущем сервере?
- Не создаст ли отдельный n8n instance лишнюю эксплуатационную нагрузку?
- Какие данные JM/EC являются персональными и должны быть удалены из company inventory?
- Должны ли личные системы вообще отображаться в company Baserow или достаточно infrastructure inventory без персональных данных?

---

## 19. AI Provider Registry

Data policy определяет классы данных, но не описывает характеристики конкретного model provider.

Необходимо проверить:

- Какие providers реально будут использоваться в pilot?
- Какие contractual/API settings у каждого provider определяют:
  - retention;
  - training use;
  - region;
  - DPA;
  - logging;
  - abuse monitoring;
  - deletion;
  - enterprise/API distinction?
- Может ли `INTERNAL` автоматически передаваться любому provider через gateway?
- Следует ли gateway проверять одновременно:
  - data classification;
  - provider approval;
  - model approval;
  - purpose;
  - maximum context?
- Нужен ли реестр:

```text
provider
model_id
region
data_retention
training_use
DPA_status
approved_data_classes
max_context
price_checked_at
status
```

- Где он должен храниться: GitHub config, Postgres или оба варианта?
- Какие поля должны быть machine-enforced, а какие informational?
- Нужен ли отдельный approval для CONFIDENTIAL независимо от provider profile?

---

## 20. Цены моделей и сервисов в архитектуре

В основном файле зафиксированы конкретные цены Google Workspace и AI models.

Необходимо проверить:

- Должна ли implementation architecture содержать изменяемые цены?
- Как быстро они будут устаревать?
- Нужны ли отдельные файлы:

```text
config/model-catalog.yaml
config/service-costs.yaml
```

- Следует ли оставить в `ARCHITECTURE.md` только:
  - policy выбора;
  - budget cap;
  - benchmark procedure;
  - ссылку на актуальный catalog?
- Кто и как обновляет `checked_at`, price и source?
- Должны ли workflows автоматически читать эти цены для cost ledger?
- Не является ли отдельный catalog преждевременным усложнением для трёх моделей?

Не переносить данные до оценки того, используется ли архитектура как единственный operational document.

---

## 21. Content approval и lifecycle

Необходимо проверить:

- Кто является canonical owner поля `Content_Items.status`?
- Может ли n8n изменить `status` на `approved` или `published` только после canonical Postgres approval?
- Как предотвращается ручное изменение статуса в Baserow?
- Нужно ли разделить:
  - workflow state;
  - review state;
  - publication state?
- Что происходит при `Needs edit` после approval?
- Создаётся ли новая artifact version или обновляется прежний draft?
- Может ли WordPress draft быть создан до approval?
- Что именно означает `approved snapshot`, если исходный draft — Google Doc?
- В каком формате выполняется snapshot/export?
- Как обрабатываются изображения и media assets, относящиеся к approved content?
- Нужно ли отдельно hash-ировать manifest и все referenced files?

---

## 22. Tamper-evident approval

Архитектура корректно не обещает физический WORM, но необходимо уточнить реализацию.

Проверить:

- Как рассчитывается canonical artifact hash для Google Doc?
- Используется ли export в стабильный формат перед hash?
- Может ли повторный export одного неизменённого Google Doc дать другой binary hash?
- Следует ли hash-ировать:
  - canonical normalized text;
  - PDF export;
  - DOCX export;
  - source manifest;
  - bundle archive?
- Как выявляется изменение approved folder вручную?
- Что блокирует publication после hash mismatch?
- Где хранится hash verification result?
- Какой workflow периодически проверяет approved artifacts?
- Как создаётся новая approved version после изменения?

---

## 23. Website lead SLA «не позднее одного дня»

Необходимо проверить:

- Это внутреннее операционное правило или публичное обещание клиенту?
- Что считается моментом получения lead:
  - WordPress form submission;
  - n8n intake;
  - Baserow upsert;
  - alert delivery?
- Один день означает:
  - 24 часа;
  - следующий рабочий день;
  - календарный день?
- Что происходит при сбое n8n?
- Достаточно ли WordPress entry как fallback?
- Как контролируется missed lead?
- Нужно ли создавать scheduled reconciliation между WordPress form entries и Baserow?
- Не следует ли формулировать публичный SLA отдельно от внутреннего action due date?

---

## 24. WordPress как published truth

Необходимо проверить:

- WordPress является source of truth только для website content или также для LinkedIn/social publications?
- Где хранится факт публикации LinkedIn post?
- Что происходит, если публикация удалена или URL изменён?
- Должен ли Baserow хранить publication receipt, а не только URL?
- Нужна ли отдельная таблица/сущность Channel Publications?
- Достаточно ли отдельных `Content_Items` для каждого channel output?
- Как обрабатывается повторная публикация одной темы на одном channel?
- Как фиксируется published version hash?

---

## 25. Drive structure и access boundaries

Необходимо проверить:

- Достаточно ли одного Shared Drive для:
  - company;
  - commercial;
  - projects;
  - evidence;
  - content;
  - templates?
- Можно ли безопасно ограничить доступ к confidential/restricted client folders внутри одного Shared Drive?
- Не понадобится ли отдельный restricted Shared Drive раньше создания s.r.o.?
- Какие папки доступны automation service accounts?
- Кто может перемещать папки верхнего уровня?
- Нужно ли запрещать создание company документов вне Shared Drive технически или только процедурно?
- Как обрабатываются текущие файлы в personal Google Drive и OneDrive?
- Нужен ли migration register с owner, classification и destination?
- Какие документы не следует мигрировать из-за Arnex или других contractual boundaries?

---

## 26. Baserow как единый интерфейс и Postgres как machine state

Необходимо проверить:

- Не возникнет ли скрытая custom CRM в Postgres, которую невозможно понять без разработчика?
- Какие business records должны существовать только в Baserow?
- Какие machine records не должны проецироваться в Baserow?
- Нужен ли простой admin/run view для Postgres?
- Как Иван будет диагностировать ошибку без прямого доступа к базе?
- Достаточно ли таблицы `Systems_Automations` для наблюдаемости?
- Какие метрики должны обязательно отображаться:
  - last success;
  - last failure;
  - stale threshold;
  - retry count;
  - pending outbox;
  - cost;
  - owner action?
- Не нужен ли отдельный lightweight monitoring tool вместо переноса monitoring state в Baserow?

---

## 27. Workflow inventory и 81 workflow

Необходимо проверить:

- Подтверждено ли количество 81 workflow актуальным `workflow-index.json`?
- Сколько workflows реально active, enabled, deprecated или duplicated?
- Что означает число в скобках в таблице, например `45 (12)`?
- Все ли workflows имеют owner и source of truth?
- Есть ли workflow, отсутствующие в repository export, но работающие в n8n Cloud?
- Как проводится ратификация `keep / merge / archive / delete`?
- Должна ли архитектура ссылаться на динамический inventory, а не фиксировать числа?
- Как предотвращается drift между n8n Cloud, self-hosted n8n и GitHub exports?

---

## 28. Cutover n8n Cloud → self-hosted

Необходимо проверить:

- Что считается успешным shadow mode для workflow с внешними side effects?
- Как исключаются:
  - duplicate Telegram alerts;
  - duplicate Baserow records;
  - duplicate WordPress drafts;
  - duplicate emails;
  - повторная обработка media?
- Как реализуется idempotency при разных execution IDs в Cloud и self-hosted?
- Где хранится cutover state?
- Есть ли rollback path после отключения Cloud twin?
- Семь дней observation — достаточно ли для low-frequency workflow?
- Следует ли observation задавать количеством успешных execution, а не только временем?
- Как переносится encryption key и credentials без экспорта secrets в Git?
- Как сравниваются outputs Cloud и self-hosted?

---

## 29. Agent control plane и business artifact mode

Необходимо проверить:

- Действительно ли текущий `ai-dev-loop-control-plane` архитектурно подходит для non-Git business tasks?
- Какие части можно переиспользовать:
  - admission;
  - policy;
  - execution;
  - evidence;
  - validation;
  - review?
- Какие части слишком tightly coupled к:
  - Git branches;
  - worktrees;
  - tests;
  - PR lifecycle?
- Не станет ли добавление `business_artifact` чрезмерным усложнением generic control plane?
- Может ли business artifact orchestration быть проще реализована в `adapteng-automation-platform`, используя control plane только как execution engine?
- Где должны находиться:
  - BusinessTaskEnvelope;
  - ArtifactEnvelope;
  - domain skills;
  - approval adapter;
  - model gateway?
- Как избежать дублирования orchestration между n8n и control plane?
- Кто отвечает за retry, timeout и idempotency: n8n или agent?

---

## 30. Pilot Content & Case Draft Assistant

Необходимо проверить pilot criteria:

- Что считается `representative draft`?
- Откуда взять 20 кейсов, если у новой компании пока нет активных клиентов и проектов?
- Можно ли использовать:
  - sanitized historical cases;
  - synthetic cases;
  - public technical sources;
  - existing AdaptEng content?
- Как измеряется `reasonable edits`?
- Как измеряется экономия времени?
- Кто размечает factual traceability?
- 70% acceptance — достаточный ли threshold?
- Следует ли разделить eval на:
  - schema validity;
  - factual grounding;
  - style;
  - usefulness;
  - editing time;
  - cost?
- Может ли pilot пройти формально, но не дать бизнес-ценности?
- Какие stop criteria применяются после первых 5–10 outputs?

---

## 31. Model gateway и direct-call bypass

Необходимо проверить:

- Все ли production workflows действительно могут быть переведены на один gateway?
- Есть ли текущие direct calls к OpenAI/Gemini/Claude в n8n workflows?
- Как они будут найдены и запрещены?
- Может ли gateway enforcement быть техническим, а не только архитектурным правилом?
- Нужны ли network restrictions или отдельные credentials?
- Где хранится per-domain budget:
  - company;
  - JM;
  - EC?
- Что происходит при budget exhaustion:
  - task pending;
  - fallback local;
  - downgrade model;
  - manual approval?
- Как предотвращается бесконечный retry при недоступности provider?
- Какие данные попадают в cost ledger?

---

## 32. Data classification и реальные документы

Необходимо проверить:

- Кто присваивает classification?
- Может ли automation присвоить её самостоятельно?
- Что происходит, если classification отсутствует?
- Должна ли система fail-closed?
- Как классифицируются:
  - публичные мануалы;
  - vendor quotations;
  - client process data;
  - stack measurements;
  - фотографии оборудования;
  - contracts;
  - emails;
  - CV и personal job data?
- Как различаются company INTERNAL и client CONFIDENTIAL?
- Может ли один artifact содержать данные разных классов?
- Нужна ли автоматическая DLP-проверка или достаточно human classification на первом этапе?
- Как classification наследуется от source к draft и approved artifact?

---

## 33. File intake и quarantine

Архитектура откладывает confidential attachment intake до появления quarantine pipeline.

Необходимо проверить:

- Где будут физически находиться quarantined files?
- Нужен ли отдельный object storage или можно использовать isolated local volume?
- Какой malware scanner предполагается?
- Какие file types разрешены?
- Как обрабатываются ZIP, password-protected archives, macros и CAD files?
- Кто принимает решение `accepted / rejected / manual_review`?
- Как предотвращается передача содержимого документа агенту до classification?
- Нужно ли это реализовывать в первые 90 дней, если website attachments пока запрещены?
- Можно ли оставить этот раздел как future requirement без включения в backlog?

---

## 34. Cost model

Необходимо проверить:

- Учтена ли фактическая стоимость:
  - Google Workspace;
  - Hetzner;
  - Cloudways;
  - Zoho;
  - offsite backup;
  - domain;
  - n8n Cloud на период migration;
  - API models;
  - возможный storage growth?
- Почему incremental cost считается отдельно от total operational cost?
- Нужны ли две метрики:
  - current total monthly stack cost;
  - new incremental Company OS cost?
- Где будут храниться invoice, renewal date, cancellation path и owner?
- Достаточно ли `Systems_Automations` или нужна таблица Services/Subscriptions?
- Не следует ли отложить отдельную таблицу до появления нескольких recurring services?
- Как учитываются annual subscriptions и VAT?

---

## 35. Definition of done

Необходимо проверить, можно ли объективно доказать каждый критерий:

1. Все новые company documents находятся в company-owned Shared Drive.
2. Baserow показывает все активные сущности.
3. Existing case/article automations используют stable IDs.
4. Один workflow безопасно работает на self-hosted n8n.
5. Control plane поддерживает business artifact.
6. Content Assistant проходит pilot.
7. Иван остаётся final approver.
8. Incremental cost видим и оправдан.
9. Restore продемонстрирован.
10. Новый человек понимает систему менее чем за час.

Вопросы:

- Какое evidence подтверждает каждый пункт?
- Кто принимает критерий?
- Что значит «все» для active relationships, systems и documents?
- Нужно ли включить checklist или acceptance test?
- Пункт про нового человека реалистичен, если система пока рассчитана на одного пользователя?
- Следует ли заменить его на документированный onboarding walkthrough?

---

## 36. Приоритеты: что проверять в первую очередь

Перед любыми изменениями рекомендуется проверить вопросы в следующем порядке:

### P0 — могут повлиять на безопасность или целостность данных

1. Где реально хранятся contracts и client evidence.
2. Enforcement approval и невозможность обхода через Baserow.
3. Backup n8n, Baserow и Postgres, включая off-host restore.
4. Доступы Baserow Free и Google Workspace.
5. Разделение company и personal automations.
6. Service account permissions в Shared Drive.
7. Canonical source of truth и reconciliation.

### P1 — могут привести к переделке data model

1. `Actions` и `next_action`.
2. Canonical UUID и display IDs.
3. Связи Actions.
4. Organization roles.
5. Opportunity stages.
6. Sources/source refs.
7. Document versions.

### P2 — можно уточнить после запуска базовой системы

1. AI Provider Registry.
2. Отдельный model/service catalog.
3. Расширенная GDPR-модель.
4. Quarantine pipeline.
5. Отдельные publication entities.
6. Расширенный monitoring interface.

---

## 37. Требуемый формат ответа AI

По результатам проверки AI должен подготовить отчёт, а не сразу переписывать `ARCHITECTURE.md`.

Рекомендуемый формат:

```markdown
## Пункт X — название

**Статус:** подтверждено / частично / не подтверждено / неактуально

**Что проверено:**
- документация;
- текущий код;
- конфигурация;
- существующий workflow;
- ограничения тарифа.

**Фактический вывод:**
Краткий вывод без предположений.

**Риск текущего решения:**
Конкретный риск либо `существенного риска не выявлено`.

**Рекомендация:**
- оставить как есть;
- уточнить формулировку;
- изменить data model;
- перенести в future backlog;
- требуется решение владельца.

**Затрагиваемые элементы:**
- разделы ARCHITECTURE.md;
- таблицы;
- repositories;
- workflows;
- migrations.

**Предлагаемая правка:**
Точный фрагмент или diff, но не применять автоматически.
```

После отчёта необходимо сформировать три отдельные группы:

1. **Подтверждённые обязательные исправления.**
2. **Рекомендации, которые требуют решения Ивана.**
3. **Замечания, которые не подтвердились или нецелесообразны.**

Только после отдельного согласования разрешено обновлять `ARCHITECTURE.md`.
