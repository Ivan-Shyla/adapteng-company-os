# AI Workforce Plan — Amendment v2.1

> **Версия:** 2.1-amendment к AI_WORKFORCE_PLAN.md v2.0-draft
> **Дата:** 2026-07-23
> **Статус:** готовые секции для вставки в мастер-файл после решения владельца
> **Принцип amendment:** ничего из safety spine v2.0 не ослабляется; меняется
> приоритизация под текущую стадию (наращивание базы, поиск клиентов) и
> добавляется тест реальной пользы

Формат: каждая секция помечена — **ЗАМЕНА** (заменяет секцию v2.0 целиком),
**ВСТАВКА** (новая секция, указано место), **ДОПОЛНЕНИЕ** (добавить в конец
существующей секции).

---

## [ВСТАВКА после 3.1] 3.2 Тест реальной пользы («chat test»)

Задача становится работой AI-сотрудника, только если проходит минимум три из
четырёх критериев. Иначе это разовый чат-запрос владельца, и строить под неё
профиль/скиллы/пайплайн — театр AI.

| Критерий | Вопрос | Пример «проходит» | Пример «не проходит» |
|---|---|---|---|
| **Рекуррентность** | Повторяется ли работа без запроса владельца? | Еженедельный скан новых тендеров | «Какие сертификации нужны CEMS-компании» — разовый research |
| **Дедлайны/мониторинг** | Есть ли внешние сроки, которые человек пропускает? | Early-bird, CFP, tender deadline, expiry сертификата | Ответ на вопрос без срока |
| **Накопление состояния** | Растёт ли структурированный реестр, ценный сам по себе? | Event register, qualification gap register, evidence register | Одноразовый текстовый ответ |
| **Связь с данными компании** | Использует ли результат account list, проекты, evidence? | Скоринг события по присутствию целевых аккаунтов | Общий список конференций из интернета |

**Прямые следствия для идей владельца (2026-07-23):**

1. «Поиск нужных сертификаций» — **не проходит** тест как сотрудник.
   Выполняется один раз как research-сессия + решение владельца.
   Рекуррентное ядро идеи — **qualification-requirements monitoring** из
   тендеров/RFQ — проходит тест и включается в Scout (см. 6-bis).
2. «Подготовка к сертификации» — это существующие скиллы Compliance Officer
   (`sop-drafter`, `audit-checklist`, `requirement-mapper`, `deadline-monitor`).
   Отдельный сотрудник не нужен.
3. «Регистрация и прохождение сертификации» — **A4, запрещено** (approval
   matrix 13.3: подписать/submit — нет; оплата — нет). AI готовит пакет,
   человек подаёт.
4. «Поиск событий + расписание must-attend» — **проходит** тест (дедлайны,
   реестр, связь с account list). Включается в Scout.
5. «Авто-регистрация на события» — **не проходит** экономически (≈5–15
   регистраций/год × 10 мин) и содержит A4-элементы (оплата, персональные
   данные). Заменяется на draft-registration pack на A1 + подача через A3
   approval, где форма это позволяет без оплаты.

---

## [ЗАМЕНА] 5.2 Рекомендуемый штат

Изменение против v2.0: добавлен **Market & Presence Scout**; приоритет очереди
пересчитан под стадию «наращивание базы и поиск клиентов» с учётом readiness
(у Scout нет client-data preconditions вообще).

| Очередь | Сотрудник | Конкретная работа | Польза | Уровень старта | Preconditions |
|---|---|---|---|---|---|
| **1 (fast track)** | **Market & Presence Scout** | Мониторинг тендеров, событий, сигналов по целевым аккаунтам; qualification gap register; weekly action brief | Ранняя видимая польза для продаж; кормит account scoring | A0/A1 | Только публичные данные; нужен утверждённый account list и keyword set |
| **1 (core track)** | **CEMS Compliance & Documentation Officer** | Project dossier, evidence register, gap analysis, SOP/checklist drafts, deadlines | Фундамент знаний; готовность к ISO 9001 и тендерным квалификациям | A0/A1 | De-identified dossier, expected profile, golden cases, reviewer (Phase A) |
| **2** | **Bid & Proposal Engineer** | Разбор RFQ/tender, compliance matrix, assumptions/exclusions, proposal draft | Быстрее предложения | A1 | Один de-identified RFQ |
| **3** | **Project Delivery Coordinator** | Project setup, deliverable register, reminders, handover pack | Меньше потерь и задержек | A1/A2 | Работающий Officer |
| **4** | **Service Knowledge Engineer** | Symptom/cause/action/result, похожие случаи, report drafts | Переиспользование опыта; связка с Reliability Desk runbooks | A0/A1 | Начало сервисных тикетов |
| Уже есть | **Marketing Evidence Worker** | Draft case/LinkedIn/website из approved evidence | Growth без выдуманных claims | A1/A3 | — |
| Позже | Lead & CRM Assistant, Finance & Procurement, Coordinator | — | — | — | По измеренному bottleneck |

**Почему два трека одновременно не нарушают правило «один specialist до
доказанной пользы»:** это правило защищает от (а) размазывания review-времени
владельца и (б) преждевременной инфраструктуры. Scout работает только на
публичных данных — trust boundary тривиальна, client isolation не нужна,
тяжёлый document pipeline не нужен. Его review-нагрузка — один weekly brief.
Если через 4 недели brief не читается или не порождает действий — Scout
останавливается первым (см. kill-критерий в 6-bis.6).

---

## [ВСТАВКА после раздела 6] 6-bis. Market & Presence Scout

### 6-bis.1 Миссия

> Еженедельно превращать публичные источники (тендеры, события, новости целевых
> аккаунтов) в короткий action brief с дедлайнами, чтобы AdaptEng не пропускал
> возможности продаж и присутствия — без единого внешнего действия без
> approval.

### 6-bis.2 Три потока работы

**Поток 1 — Tender & Qualification Monitor**

- Источники (кандидаты, финализирует владелец): TED (tenders.europa.eu) по
  CPV-кодам мониторинга выбросов/газоанализа и keyword set (CEMS, AMS,
  continuous emission, kontinuální měření emisí, QAL); чешские
  NEN / Věstník veřejných zakázek; словацкий UVO; goszakup.gov.kz для Arnex.
- Выход еженедельно: новые релевантные тендеры со scoring (соответствие
  компетенциям, география, дедлайн, требуемые квалификации).
- **Qualification gap register:** из каждого тендера извлекаются требуемые
  сертификаты/допуски/референсы. Реестр копит частоту: «ISO 9001 требовался в
  N из M тендеров за квартал». Решение о сертификации принимается по этим
  данным, а не по общим соображениям.
- Правило: extraction всегда с citation на тендерную документацию; строка без
  источника = `unknown`, не догадка.

**Поток 2 — Event Register & Presence Pipeline**

- Источники (кандидаты, проверить актуальность до включения): отраслевые
  конференции CEM/AQE-серии, IFAT, Enlit/энергетические выставки, MSV Brno,
  чешские конференции по ochraně ovzduší, WtE-события, вендорские дни
  (DURAG/SICK/ABB user days). Список живой — Scout сам предлагает новые
  кандидаты в pending, владелец утверждает включение в мониторинг.
- Каждое событие в реестре: даты, город, стоимость, дедлайны (early-bird, CFP,
  стенд), формат участия и **presence score**:

| Критерий | 0 | 1 | 2 |
|---|---|---|---|
| Целевые аккаунты из account list среди участников/экспонентов | нет данных | 1–2 | 3+ или ключевой аккаунт |
| OEM/интеграторы-партнёры присутствуют | нет | косвенно | подтверждённо |
| Тематическое совпадение (CEMS/AMS/compliance) | смежное | частичное | ядро |
| Стоимость участия против бюджета квартала | высокая | средняя | низкая/бесплатно |
| Возможность выступить (CFP) | нет | постер/панель | доклад |

- ≥7 баллов → кандидат в must-attend; владелец утверждает → событие получает
  дедлайн-трекинг и draft-registration pack.
- **Draft-registration pack (A1):** предзаполненные регистрационные данные,
  черновик CFP-заявки при наличии, calendar hold, ориентировочная стоимость
  поездки, напоминание за 7 дней до каждого дедлайна.
- **Подача регистрации:** только A3 (одноразовый approval), и только формы без
  оплаты. Любая оплата — человек. Автономная регистрация (A4) не планируется.

**Поток 3 — Account Signals**

- По утверждённому account list (30 аккаунтов из Reliability Desk roadmap):
  публичные сигналы — модернизации, экологические разрешения, инвестиционные
  анонсы, тендеры самого аккаунта, кадровые изменения environmental/E&I ролей.
- Выход: строка в weekly brief «аккаунт → сигнал → предлагаемое действие →
  источник». Никаких выводов о «боли» без источника — только факт + гипотеза,
  помеченная как гипотеза.

### 6-bis.3 Что Scout никогда не делает

- не отправляет письма, заявки и регистрации без одноразового approval;
- не платит и не вводит платёжные данные (A4);
- не скрейпит источники в обход их условий использования; проблемные источники
  выносятся в pending-решение владельца;
- не делает выводов о компаниях без citation;
- не касается клиентских/проектных данных — работает только с публичными
  источниками и утверждённым account list.

### 6-bis.4 Task contract (пример)

```yaml
task_id: SCOUT-0001
employee_id: market-presence-scout
task_type: weekly-market-brief
risk: low
autonomy: A1
owner: ivan
schedule: weekly (mon 06:00 Europe/Prague)

inputs:
  account_list_ref: sheets://approved/account-list-v1
  keyword_set_ref: git://adapteng-scout/keywords.yaml
  event_register_ref: postgres://scout.events

expected_output:
  schema: weekly-market-brief.v1
  sections: [tenders, events_deadlines, account_signals, qualification_gaps]
  artifact_target: telegram-draft + sheets append

limits:
  max_model_calls: 40
  max_cost_eur: 3
  max_duration_minutes: 45

capabilities:
  read_public_sources: true
  create_draft: true
  send_external: false
  register_external: false

required_gates:
  - source_citation
  - output_schema
  - no_unsupported_claims
```

### 6-bis.5 KPI

| KPI | Pilot target |
|---|---|
| Citation coverage factual rows | 100% |
| Пропущенный дедлайн утверждённого must-attend события | 0 |
| Тендеры, найденные Scout раньше, чем владельцем | измерять; цель ≥1/мес |
| Действия владельца, порождённые brief (заявка, контакт, регистрация) | ≥2/мес после 4 недель |
| Ложноположительные тендеры в brief | ≤30% после тюнинга keyword set |
| Cost per weekly brief | измерять; лимит €3 |
| Время владельца на чтение brief | ≤15 мин/нед |

### 6-bis.6 Kill-критерий (обязателен)

Если после 6 недель brief не порождает минимум 2 реальных действий в месяц
или владелец перестал его читать — Scout останавливается и разбирается причина
(источники, скоринг, стадия компании). Продолжать «потому что уже построили» —
запрещено. Это прямое применение раздела 15 («не строить театр AI»).

### 6-bis.7 Инфраструктура

Scout не требует document pipeline, client isolation и Coolify:
n8n schedule → fetch публичных источников → AI Gateway (classification +
extraction по схеме) → Postgres (event register, tender log, qualification
register; до Phase E допустим local SQLite) → weekly brief в Telegram draft.
Это сознательно самый лёгкий сотрудник в штате: его цель — доказать бизнес-
пользу workforce-подхода за недели, а не месяцы.

---

## [ДОПОЛНЕНИЕ к 6.3] Новый скилл Compliance Officer

| Skill | Вход | Выход | Обязательная проверка |
|---|---|---|---|
| `certification-readiness-pack` | Утверждённый target-сертификат (напр. ISO 9001) + evidence register | Gap list против требований, план подготовки, draft-комплект документов к подаче | Human верифицирует интерпретацию требований; подача — только человеком |

Вход появляется из qualification gap register Scout: сертификация выбирается
по частоте требований в реальных тендерах, а не по общему списку.

---

## [ДОПОЛНЕНИЕ к 14] Phase A-fast — Scout pilot (параллельно Phase A)

**Entry:** утверждённый account list (30 аккаунтов), keyword set, список
источников, kill-критерий подписан владельцем.

**Работа (2–3 недели):**

1. keywords.yaml + список источников в новом лёгком private repo
   `adapteng-scout` (или каталоге в `adapteng-automation-platform` — решение
   владельца по границе доверия; отдельный runtime-доступ не нужен);
2. n8n workflow: TED + NEN + 3–5 event-источников;
3. схема weekly-market-brief.v1 и валидация через Gateway (если Gateway ещё
   не исполняем — временный direct call с local run manifest, как в Phase D);
4. первые 2 brief в shadow (владелец сверяет с тем, что нашёл бы сам);
5. включение живого weekly brief.

**Exit gate:** 4 недели подряд brief выходит вовремя, citation coverage 100%,
владелец совершил ≥2 действия из brief.

**Явное ограничение:** Phase A-fast не потребляет review-ёмкость Phase A–D
(один brief в неделю) и не открывает никаких write/external capabilities.
Если возникает конфликт приоритетов по времени владельца — core track
(Compliance Officer ladder) важнее, Scout ставится на паузу, а не наоборот:
Scout — усилитель продаж, core track — фундамент компании.

### [ЗАМЕНА] 90-дневный ориентир

| Период | Core track | Fast track (Scout) |
|---|---|---|
| Days 1–14 | Решения, de-identified project copy, expected profile, baseline, TASK-016 review | Account list + keywords утверждены; источники выбраны |
| Days 15–35 | TASK-017 + domain repo + schemas/fixtures | Workflow + 2 shadow briefs; включение live brief |
| Days 36–55 | Offline dossier MVP + eval | Тюнинг false positives; qualification register наполняется |
| Days 56–75 | Coolify shared runtime + restore proof | Перенос Scout state в общий Postgres |
| Days 76–90 | Read-only real-data pilot, go/no-go | Kill-criterion review: ≥2 действия/мес или пауза |

Сроки — условный ориентир; exit gates важнее календаря (без изменений к v2.0).

---

## [ДОПОЛНЕНИЕ к 17] Новые решения владельца

- [ ] **Scout:** утвердить account list, keyword set, источники и kill-критерий.
- [ ] **Sertification research:** провести один research-сеанс (не сотрудник) и
  зафиксировать shortlist кандидатов; решение о первой сертификации — только
  после 8+ недель данных qualification gap register, кроме случая, когда
  конкретный живой тендер требует её раньше.
- [ ] **События:** утвердить бюджет поездок на квартал (без него presence score
  по стоимости не считается).
- [ ] **Registration boundary:** подтвердить правило «оплата — всегда человек;
  подача формы без оплаты — A3 approval».

---

## Что сознательно НЕ изменено против v2.0

- Safety spine, уровни автономии, approval matrix, kill switch — без изменений.
- Compliance Officer остаётся core-фундаментом; Scout его не заменяет и не
  отодвигает его preconditions.
- Правило «репозиторий по границе доверия» соблюдено: Scout не получает доступ
  к client data и не входит в `adapteng-compliance`.
- Запрет A4 подтверждён и расширен явными примерами: подача сертификационных
  документов, оплата регистраций, автономная регистрация на события.
