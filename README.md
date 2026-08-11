# AdaptEng Company OS

Единая точка входа в операционную систему AdaptEng.

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — утверждённая архитектура, рабочий
  интерфейс, хранилище, автоматизации, AI-контур и план 30/60/90 дней.

**Рабочие поверхности:** [Baserow Company Operations](https://baserow.adapteng.com)
для статусов/решений и [Shared Drive AdaptEng Company](runbooks/company-drive.md#governed-folder-aliases)
для файлов. Новые материалы компании не загружать в личный My Drive; точные
папки указаны в [`runbooks/company-drive.md`](runbooks/company-drive.md).

В этом репозитории нет клиентских документов, персональных данных, паролей,
runtime-дампов и копий реализации из других репозиториев. Изменения архитектуры
делаются через PR и обновляют существующий master-файл, а не создают новый
параллельный план. Принцип зафиксирован в
[`decisions/0001-…`](decisions/0001-company-os-is-index-not-implementation.md).
Граница личных проектов зафиксирована в
[`decisions/0002-…`](decisions/0002-personal-projects-remain-outside-company-os.md).

## Структура репозитория (operating structure)

`ARCHITECTURE.md` — это «почему» и план. Остальные папки — операционный слой:
индекс живой реальности, процедуры, решения и то, что требует владельца.

| Папка | Что внутри |
|---|---|
| [`control-plane/`](control-plane/) | Межрепозиторный слой: проверенное текущее состояние и реестр расхождений, политика автономии (что агент делает сам), аудит защит P0–P3 и программа работ. |
| [`registry/`](registry/) | Живой индекс: `services.yaml`, `workflows.yaml`, `data-stores.yaml`, `environments.yaml` — что существует, где и в каком статусе (только id/имена, без секретов). |
| [`runbooks/`](runbooks/) | Повторяемые процедуры: операции с n8n, применение миграций, backup/restore, ротация секретов, реагирование на инциденты. |
| [`decisions/`](decisions/) | ADR-журнал уровня компании + шаблон; ссылки на платформенные ADR в `adapteng-automation-platform`. |
| [`ai/`](ai/) | Программа AI: точки встраивания, guardrails, выбор модели с проверенными ценами, контроль затрат. |
| [`owner/`](owner/) | «Пины» — действия только для владельца (`action-items.md`) и карта доступов по именам (`access-map.md`). |
| [`deploy/`](deploy/) | Декларативное целевое состояние деплоя: один файл на сервис для Coolify. Значения применяет [`scripts/coolify_deploy.py`](scripts/coolify_deploy.py) через workflow `Coolify deploy`; секретные значения — только по имени. |

## Быстрые ответы (где смотреть)

| Вопрос | Файл |
|---|---|
| Что уже живое / где сервис? | [`registry/services.yaml`](registry/services.yaml), [`registry/workflows.yaml`](registry/workflows.yaml) |
| Какие таблицы Baserow / миграции применены? | [`registry/data-stores.yaml`](registry/data-stores.yaml) |
| Куда загружать фото, видео, кейсы и drafts? | [`runbooks/company-drive.md`](runbooks/company-drive.md) |
| Как безопасно поменять workflow / применить миграцию? | [`runbooks/`](runbooks/) |
| Как ротировать токен (и почему старый ещё живой)? | [`runbooks/secret-rotation.md`](runbooks/secret-rotation.md) |
| Что должен сделать Иван? | [`owner/action-items.md`](owner/action-items.md) |
| Что агент может делать без approve? | [`control-plane/autonomy-policy.md`](control-plane/autonomy-policy.md) |
| Что реально блокирует работу прямо сейчас? | [`control-plane/current-state.md`](control-plane/current-state.md) |
| Какие работы запущены и в каком порядке? | [`control-plane/execution-program.md`](control-plane/execution-program.md) |
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

## Проверки перед PR

Те же команды выполняет CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml))
на каждый pull request и push в `main`. Внешних зависимостей нет — только
стандартная библиотека Python (проверено на 3.11; минимум 3.9) и `git`.
Запускать из корня репозитория:

```bash
python scripts/validate_sensitive_references.py
python -m unittest scripts.test_validate_sensitive_references scripts.test_postgres_restore_rehearsal scripts.test_rehearsal_contour scripts.test_rehearsal_effective_repository scripts.test_coolify_deploy scripts.test_postgres_runtime_role scripts.test_ops_runner scripts.test_pre_pr_commands_match_ci
python -m unittest scripts.test_postgres_restore_scheduler_surface  # только POSIX
```

Первая команда — это то, что делает проверяемым обещание из начала файла: она
читает отслеживаемые файлы через `git ls-files` и возвращает код 1, если находит
ссылку или id ресурса Google Drive либо присвоение чувствительного значения.
Вторая — регрессионные наборы к ней, к процедуре восстановления PostgreSQL, к
её контуру и выбору репозитория, и к драйверу деплоя в Coolify.
`unittest discover` не используется: в `scripts/` нет `__init__.py`, поэтому
модули перечисляются явно, а запуск обязателен из корня репозитория.

Список модулей во второй команде должен совпадать с
[`ci.yml`](.github/workflows/ci.yml) — иначе локальная проверка слабее CI и
пропускает то, что CI обязательно поймает. Это не просьба: набор
`test_pre_pr_commands_match_ci` разбирает оба файла и падает при расхождении в
любую сторону. Добавляя модуль в CI, добавьте его и сюда.

Третья команда работает только на POSIX и на Windows не запустится: её предмет —
`scheduler_file_record`, который открывает файлы с `os.O_NOFOLLOW` (на Windows
такого флага нет), а фикстуры используют `os.symlink` (Windows требует
привилегию). Маркеров пропуска в ней нет: набор целиком выполняется в CI на
`ubuntu-latest` — на той платформе, где экспортёр и работает.