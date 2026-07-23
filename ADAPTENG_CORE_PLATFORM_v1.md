# AdaptEng Core Platform v1.0
## Единая база для автоматизаций и AI-агентов

> **Версия:** 1.0 (консолидация AI Operator v3.x + Workforce Plan v2.0 + Amendment v2.1)
> **Дата:** 2026-07-23
> **Статус:** заменяет собой дальнейшие итерации архитектурных документов.
> Изменения после утверждения — только ADR-дельтами ≤1 страницы.
> **Правило anti-drift:** новая версия этого документа запрещена, пока не
> отгружен Slice 2 (см. §8). Архитектура дальше улучшается кодом и ADR.

---

## 1. Что такое «база» — одно определение

База — это НЕ штат из 9 сотрудников и не набор репозиториев. База — это
**6 слоёв-контрактов**, поверх которых любой агент, автоматизация или чат-сессия
собираются за дни, а не недели:

```text
L0  Context Canon      — что компания знает о себе (факты, границы, тон)
L1  Data Spine         — где живёт состояние и документы (Postgres + Drive)
L2  Event/Task Queue   — как любая работа входит в систему (одна очередь)
L3  Gateway-lite       — как вызывается модель (схема, стоимость, лог)
L4  Approval & Action  — как результат становится действием (карточки, адаптеры)
L5  Value Ledger       — как измеряется польза (и когда что-то убивается)
```

Сотрудник (Scout, Compliance Officer, Bid Engineer) = **тонкий YAML-профиль +
набор скиллов** поверх этих слоёв. Ни один сотрудник не владеет
инфраструктурой. Safety spine из Workforce Plan v2.0 (уровни A0–A4, approval
matrix 13.3, kill switch 13.4, provenance-правила) действует без изменений и
здесь не пересказывается.

---

## 2. L0 — Context Canon (новый слой; максимальный рычаг)

### 2.1 Проблема, которую он решает

Каждая чат-сессия, каждый агент и каждый workflow сейчас заново узнаёт, что
такое AdaptEng, что можно утверждать публично, каким тоном писать и кто целевые
клиенты. Это главная причина, почему AI-результаты «generic» и требуют правок.

### 2.2 Состав (Git, английский — файлы читаются агентами)

```text
/context/
  COMPANY.md          # факты: юрлица, каналы Adapteng/Arnex, география, роли
  SERVICES.md         # что продаём и ЧТО НЕ ДЕЛАЕМ (exclusions из Reliability Desk)
  CLAIMS_APPROVED.md  # единственный источник публичных утверждений:
                      # сертификации, референсы, цифры. Нет в файле = нельзя писать
  ACCOUNTS.md         # указатель на account list (Sheet) + правила скоринга
  REGULATORY_MAP.md   # рабочая карта: IED/EN 14181/NIS2/CBAM/KZ — только как
                      # список тем и ссылок, не юридические заключения
  TONE_STYLE.md       # тон, язык B1-B2 для EN, форматы писем/КП
  GLOSSARY.md         # CEMS/АСМ/QAL термины RU-EN, названия систем
  BOUNDARIES.md       # что AI не делает никогда (выжимка A4 + client isolation)
```

### 2.3 Правила слоя

- один владелец — Ivan; изменения только через PR (история = аудит);
- каждый task contract указывает, какие context-файлы загружены (provenance);
- `CLAIMS_APPROVED.md` — жёсткий контракт: marketing/proposal-скиллы могут
  использовать публичные утверждения ТОЛЬКО отсюда; это машинная реализация
  правила «no unsupported claims»;
- те же файлы подключаются в Claude Projects / Claude Code / ChatGPT — Canon
  обслуживает и агентов, и ручные сессии (решает задачу /context/ directory);
- в Canon нет клиентских данных, цен и секретов — он INTERNAL, не CONFIDENTIAL.

**Почему это первый рычаг:** Canon улучшает КАЖДОЕ AI-взаимодействие компании
с первого дня, не требует ни очереди, ни Gateway, ни Postgres, и собирается из
уже написанных документов (Reliability Desk master, Workforce Plan, сайт) за
2–4 дня.

---

## 3. L1 — Data Spine

- **Postgres — канон операционного состояния:** tasks, runs, events,
  registers (event register, tender log, qualification register, evidence
  register). До Coolify допустим один managed/локальный инстанс; SQLite —
  только для offline-MVP отдельного скилла.
- **Drive — документы** по структуре v2.0 §9.3; Git — код, схемы, шаблоны,
  Canon; Sheets — только человеческая витрина, никогда не источник истины.
- **Обязательные поля каждой записи:** `source_ref`, `classification`,
  `created_by (human|agent+run_id)`, `verified_by (nullable)`. Без source_ref
  запись не создаётся — это уже принято в v2.0 и не обсуждается.
- **Реестры — сердце пользы.** Ценность агентов на 80% в том, что реестры
  наполняются и не протухают. Каждый реестр имеет owner-человека и правило
  устаревания (stale after N days → строка помечается, попадает в weekly brief).

---

## 4. L2 — Event/Task Queue: один вход для любой работы

Всё, что происходит, становится событием в одной очереди:

| Источник | Событие | Что порождает |
|---|---|---|
| Gmail (label rule) | inbound RFQ / client mail | task proposal: extract + classify |
| Cron (n8n) | weekly scout tick | task: weekly-market-brief |
| Telegram-команда | «проверь досье X» | task proposal с contract |
| Форма сайта (3 формы из Reliability Desk plan) | lead / support / partner | task: qualify + draft reply |
| GitHub PR | canon change | task: re-validate dependent claims |
| Reliability Desk (будущее) | ticket P1–P4 | task: triage draft |

Правила: очередь живёт в Postgres (n8n — исполнитель, не источник истины);
каждое событие → task contract по схеме v2.0 §4.3; deny-by-default — событие
без подходящего профиля/скилла попадает в pending-решение владельца, а не
исполняется «как получится». Один паттерн для всего — вот что делает базу
базой: новая автоматизация = новый маппинг «событие → скилл», не новый проект.

---

## 5. L3 — Gateway-lite (прагматичная версия)

Полный AI Gateway из ADR-0003 не должен блокировать пользу. Gateway-lite —
один модуль (n8n sub-workflow или небольшой сервис), через который идут ВСЕ
model calls:

- вход: skill_id, schema_id, context_refs, лимиты из contract;
- выход: JSON по схеме или отказ (schema validation — детерминированная);
- лог каждого вызова: модель, токены, стоимость, run_id → Postgres;
- маршрутизация v1 примитивная: дешёвая модель для classification, сильная
  для extraction/drafts; таблица маршрутов в Git;
- никаких прямых вызовов моделей из workflow мимо модуля — это правило, а не
  технология.

Апгрейд до полного Gateway (кэш, провайдер-абстракция, budget enforcement) —
после того как через lite прошло ≥500 вызовов и стало видно, что реально нужно.

---

## 6. L4 — Approval & Action

- **Один inbox — Telegram.** Карточка approval: что сделано, источники,
  стоимость, diff если правка, кнопки Approve / Edit / Reject. Решение
  пишется в audit. Единственное место, где ты взаимодействуешь с workforce
  ежедневно.
- **Action adapters — белый список:** gmail_draft, gmail_send(approved),
  drive_write(drafts), wp_draft, sheet_append, calendar_hold,
  registration_submit(no-payment, approved). Каждый адаптер — идемпотентный,
  с rollback-описанием. Новый адаптер = ADR-дельта.
- Всё внешнее — одноразовый approval token (A3). Оплаты, подписи, подача
  сертификационных документов — A4, адаптеров не существует физически.

---

## 7. L5 — Value Ledger: «работает», а не «существует»

Каждый run завершается записью: outcome (accepted / accepted_with_edits /
rejected / ignored), cost_eur, est_minutes_saved (владелец проставляет грубо,
раз в неделю в 2 клика из brief), citations_ok (bool).

- **Weekly:** одна строка в brief: runs, принято %, стоимость, минуты.
- **Monthly:** value report по формуле v2.0 §15.3.
- **Kill-правило платформы:** workflow, который 6 недель подряд не порождает
  принятых результатов или действий владельца, автоматически переходит в
  paused и требует явного решения. «Ignored» — это outcome, и он смертелен.

Это единственный механизм, который отличает работающую систему от существующей.
Он строится в Slice 1–2, не «потом».

---

## 8. Порядок сборки: 4 среза, каждый заканчивается используемым результатом

**Правило среза:** срез = 1–2 недели, проходит через несколько слоёв,
в конце — артефакт, который используется еженедельно. Не бывает среза
«построили инфраструктуру».

### Slice 1 — Context Canon + Telegram inbox (2–4 дня)

- Собрать 8 файлов Canon из существующих документов; PR-процесс.
- Подключить Canon к твоим ручным сессиям (Claude Project / Code).
- Telegram-бот: принимает команды, показывает pending-список (пока пустой).
- **Exit:** ты провёл ≥3 рабочие сессии с Canon и правок «это не про нас»
  стало заметно меньше (субъективно — ок для Slice 1).

### Slice 2 — Scout weekly brief end-to-end (1–2 недели)

- Очередь (Postgres) + cron-событие + Gateway-lite + схема brief +
  value ledger + Telegram-доставка. Спецификация — Amendment v2.1 §6-bis.
- **Exit:** 2 shadow-brief + 2 живых brief, citation 100%, стоимость видна,
  outcome-кнопки работают. Это первый агент на полной базе.

### Slice 3 — RFQ Intake (1–2 недели; прямая польза поиску клиентов)

- Gmail label → событие → extraction (требования, сроки, квалификации,
  неизвестные) → qualification register (продолжает наполняться) → draft
  clarification questions + внутренняя карточка сделки.
- Скилл №1 будущего Bid & Proposal Engineer; работает на de-identified
  правилах v2.0 (Phase H preconditions не нужны — это intake, не proposal).
- **Exit:** 3 реальных RFQ/письма обработаны быстрее ручного разбора; ты
  отправил клиенту вопросы из draft.

### Slice 4 — Approval Gate v1 + первое A3-действие (1 неделя)

- Одноразовые токены, аудит решений; адаптеры gmail_send(approved) и
  calendar_hold; первое реальное внешнее действие через approval.
- **Exit:** ≥3 внешних действия прошли цикл draft → card → approve → send →
  audit без ручного копипаста.

### После срезов — треки по готовности precondition'ов

- **Compliance Officer / dossier** — когда выполнены Phase A preconditions
  (de-identified dossier, expected profile, reviewer). Тяжёлый трек, свой темп.
- **Reliability Desk triage** — когда появятся первые тикеты пилота: событие
  ticket → severity draft + runbook match. База уже готова это принять.
- **Coolify** — когда что-то должно работать 24/7 без твоего ноутбука
  (критерий из v2.0 §12 без изменений).

---

## 9. Карта владения (что где живёт — закрыть вопрос навсегда)

| Актив | Место | Владеет |
|---|---|---|
| Canon, схемы, маршруты моделей, профили, скиллы-код | Git (`adapteng-automation-platform` + domain-repo по trust boundary) | Ivan через PR |
| Операционное состояние, очередь, реестры, ledger | Postgres | платформа |
| Документы, evidence, drafts | Drive | data policy v2.0 §9 |
| Оркестрация, расписания, адаптеры | n8n | платформа |
| Человеческий интерфейс | Telegram (+Sheets витрины) | Ivan |
| Секреты | n8n credentials / env, никогда Git | Ivan |

---

## 10. Что сознательно НЕ строим сейчас

- pgvector/RAG — пока реестры и Canon не покрывают потребность поиска;
- Workforce Coordinator и мульти-агентные диалоги — до 2+ доказанных скиллов;
- полный Gateway, дашборды кроме Sheets, Coolify — по критериям выше;
- новые версии архитектурных документов — запрещены до конца Slice 2.

---

## 11. Решения владельца для старта (5 галочек вместо 12)

- [ ] Утвердить этот документ как замену дальнейших итераций.
- [ ] Slice 1: выделить 2–4 дня, начать с COMPANY.md и CLAIMS_APPROVED.md.
- [ ] Подтвердить Postgres-инстанс для очереди/реестров (managed EU или
  локальный до Coolify).
- [ ] Утвердить account list + keyword set (вход Slice 2).
- [ ] Назначить Gmail-label правило для RFQ (вход Slice 3).
