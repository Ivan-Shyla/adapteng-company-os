# AdaptEng Company OS

Единая точка входа в операционную систему AdaptEng.

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — утверждённая архитектура, рабочий
  интерфейс, хранилище, автоматизации, AI-контур и план 30/60/90 дней.

**Рабочие поверхности:** [Baserow Company Operations](https://baserow.adapteng.com)
для статусов/решений и [Shared Drive AdaptEng Company](https://drive.google.com/drive/folders/0AC0RFKG8iI-TUk9PVA)
для файлов. Новые материалы компании не загружать в личный My Drive; точные
папки указаны в [`runbooks/company-drive.md`](runbooks/company-drive.md).

В этом репозитории нет клиентских документов, персональных данных, паролей,
runtime-дампов и копий реализации из других репозиториев. Изменения архитектуры
делаются через PR и обновляют существующий master-файл, а не создают новый
параллельный план. Принцип зафиксирован в
[`decisions/0001-…`](decisions/0001-company-os-is-index-not-implementation.md).

## Структура репозитория (operating structure)

`ARCHITECTURE.md` — это «почему» и план. Остальные папки — операционный слой:
индекс живой реальности, процедуры, решения и то, что требует владельца.

| Папка | Что внутри |
|---|---|
| [`registry/`](registry/) | Живой индекс: `services.yaml`, `workflows.yaml`, `data-stores.yaml`, `environments.yaml` — что существует, где и в каком статусе (только id/имена, без секретов). |
| [`runbooks/`](runbooks/) | Повторяемые процедуры: операции с n8n, применение миграций, backup/restore, ротация секретов, реагирование на инциденты. |
| [`decisions/`](decisions/) | ADR-журнал уровня компании + шаблон; ссылки на платформенные ADR в `adapteng-automation-platform`. |
| [`ai/`](ai/) | Программа AI: точки встраивания, guardrails, выбор модели с проверенными ценами, контроль затрат. |
| [`owner/`](owner/) | «Пины» — действия только для владельца (`action-items.md`) и карта доступов по именам (`access-map.md`). |

## Быстрые ответы (где смотреть)

| Вопрос | Файл |
|---|---|
| Что уже живое / где сервис? | [`registry/services.yaml`](registry/services.yaml), [`registry/workflows.yaml`](registry/workflows.yaml) |
| Какие таблицы Baserow / миграции применены? | [`registry/data-stores.yaml`](registry/data-stores.yaml) |
| Куда загружать фото, видео, кейсы и drafts? | [`runbooks/company-drive.md`](runbooks/company-drive.md) |
| Как безопасно поменять workflow / применить миграцию? | [`runbooks/`](runbooks/) |
| Как ротировать токен (и почему старый ещё живой)? | [`runbooks/secret-rotation.md`](runbooks/secret-rotation.md) |
| Что должен сделать Иван? | [`owner/action-items.md`](owner/action-items.md) |
| Куда встраивается AI и по какой цене? | [`ai/insertion-points.md`](ai/insertion-points.md), [`ai/model-choices.md`](ai/model-choices.md) |
| Текущий статус всего | [`ARCHITECTURE.md` §11](ARCHITECTURE.md#11-current-status) |

## Onboarding за час

Чтобы понять компанию с нуля, прочитайте по порядку:

1. [§1 Контекст и решение](ARCHITECTURE.md#1-зафиксированный-контекст-компании) —
   что за компания, кто работает, какие сервисы уже живые.
2. [§3 Рабочий интерфейс (Baserow)](ARCHITECTURE.md#3-baserow-единый-рабочий-интерфейс) —
   восемь таблиц, ежедневный ритуал `00 — Today` / `01 — Ivan Decision`.
3. [§4 Хранилище и drafts (Google Drive)](ARCHITECTURE.md#4-корпоративное-хранилище-и-drafts) —
   структура Shared Drive и что считается draft.
4. [§5 Автоматизации (n8n → Baserow/Drive)](ARCHITECTURE.md#5-подключение-n8n-к-baserow-и-drive) —
   как кейсы, статьи и лиды попадают в систему.
5. [§10 План 30/60/90](ARCHITECTURE.md#10-implementation-backlog) — что делать
   сейчас и в каком порядке.

Всё остальное (AI-контур §7, безопасность §8, стоимость §9) читается после этих
пяти шагов.