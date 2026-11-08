# hermes-todo-goal

Инструмент `goal_control`: LLM управляет Goal **текущего чата** без ручного `/goal` —
создание, чтение, изменение, пауза, продолжение и запрос проверенного завершения.
TODO необязателен.

**Статус: реализовано и проверено локально. Не установлено и не включено.**

Установка, включение в профиль, перезапуск и модельный E2E требуют отдельного
разрешения. Этот каталог сам по себе ничего не активирует.

## Зачем

Goal в Hermes — это **контракт результата**: пять полей (`outcome`, `verification`,
`constraints`, `boundaries`, `stop_when`), по которым штатный механизм продолжает
работу между ходами и проверяет результат. TODO — изменяемый план действий;
галочки не являются доказательством.

Раньше Goal задавался только слэш-командой, то есть человеком. Теперь это вызов
инструмента, а продолжение, judge, quality gates и бюджет остаются штатными: плагин
не запускает второй цикл и не пишет в SQLite напрямую.

## Действия

| action | аргументы | что делает |
|---|---|---|
| `status` | — | прочитать Goal, контракт и `revision`; ничего не пишет |
| `create` | `contract`, `expected_todo_revision?` | создать Goal; независимо от TODO |
| `update` | `contract`, `expected_revision` | заменить контракт на месте; бюджет, gates, subgoals, wait сохраняются |
| `pause` | `expected_revision`, `reason` | остановить автопродолжение (не прерывает текущий вызов) |
| `resume` | `expected_revision`, `reason` | продолжить с остатком бюджета, без сброса `turns_used` |
| `complete` | `expected_revision`, `evidence` | запросить проверку; возвращает `verification_status: pending` |

Полная семантика и коды ошибок: [skills/goal-workflow/references/tool-contract.md](skills/goal-workflow/references/tool-contract.md).

## Границы (сознательные)

- `create` не заменяет живой Goal: `active`/`paused` → отказ `goal_exists`.
- `resume` не обходит `budget`/gate/auth-остановки и не снимает wait barrier.
- `complete` не ставит `done`: вердикт даёт штатный judge на границе хода, после
  прохождения gates. Доказательства нужно повторить в ответе — judge читает ответ,
  а не результат инструмента.
- Сессия берётся из контекста хода (`bound_session_key()`), а не из аргументов
  модели: дочерний агент с `conversation_id` родителя не достанет чужой Goal.
- Каждая мутация перечитывается у хоста и сверяется по полям; потерянная запись
  даёт `persistence_unverified`, а не «успех».
- Модель не может передать `session_id`, `profile`, `home`, `db_path`, `approved`,
  `reset_budget`, команды gates — такие аргументы отвергаются до побочных эффектов.

### Известное ограничение: подтверждение `update`

В этом билде Hermes у инструмента нет доверенного канала подтверждения
(`PluginContext.ask_choice` работает только из слэш-команды, а `clarify` требует
agent-callback, которого registry-инструмент не получает). Поэтому `update`
не спрашивает диалог, а:

1. сохраняет before/after в аудит `plugin-data/hermes-todo-goal/goals/<sha256>.json`
   (ключ сессии хэшируется, последние 50 событий);
2. возвращает `notice`, который скилл обязывает показать пользователю;
3. никогда не трогает бюджет, gates, subgoals и wait.

Ограничением это является потому, что ослабление контракта не блокируется
технически. Если нужен жёсткий гейт — требуется host-API для подтверждения из
инструмента; это отдельная задача, а не работа обходным путём.

## Установка (не выполнена)

Опубликовано: https://github.com/pnpwin/hermes-todo-goal (MIT, CI на 3.11/3.12
зелёный, 62 Hermes-независимых теста в чистом клоне).

Голый путь в `hermes plugins install` не принимается: он разбирается как GitHub
`owner/repo` (`_resolve_git_url` даёт `https://github.com/Users/somovis.git` + subdir).
Два рабочих варианта:

**A. Управляемая установка из git** (запись в `plugins/.install-metadata.json`,
работают `plugins update`):

```sh
cd /Users/somovis/projects/hermes_agent/hermes-todo-goal
git init && git add -A && git commit -m "hermes-todo-goal 0.1.0"
hermes plugins install "file://$PWD" --enable
# либо после push: hermes plugins install pnpwin/hermes-todo-goal --enable
```

**B. Ручная директория** (так на этой машине живут `hermes-skillcraft`, `ast-index`):
без provenance и без авто-обновлений.

```sh
cp -R /Users/somovis/projects/hermes_agent/hermes-todo-goal ~/.hermes/plugins/
hermes plugins enable hermes-todo-goal
```

Не запускалось. После загрузки инструмент появляется как `goal_control` в toolset
`goals` (плагинные toolsets включаются автоматически, тикать `hermes tools` не нужно),
скилл — как `goal-workflow`.

### Нужен ли рестарт

- Новый процесс CLI/TUI — нет: каждый процесс обнаруживает плагины на старте.
- Уже запущенные Desktop/gateway: `install --enable` / `enable` шлёт по control
  socket `reload-plugins` (и будит backend Desktop). Если ответ пришёл, плагин
  загружается в процессе сразу, но **Python-инструменты остаются отложенными до
  следующей сессии** (`plugins_activation.py`: «Python tools and system-prompt
  sections stay deferred to the next session»). Значит: открыть новый чат.
- Если gateway не ответил (`restart_required`) — перезапуск: закрыть и открыть
  Hermes Desktop либо `hermes gateway restart`.

## Проверка

Из корня каталога:

```sh
# 1. Чистое ядро: контракт, политика, схема, регистрация (stdlib, Python 3.15t)
/Users/somovis/.pyenv/versions/3.15.2t/bin/python3.15t -B -m unittest discover -s tests \
  -p 'test_contract.py' -v
/Users/somovis/.pyenv/versions/3.15.2t/bin/python3.15t -B -m unittest discover -s tests \
  -p 'test_service.py' -v
/Users/somovis/.pyenv/versions/3.15.2t/bin/python3.15t -B -m unittest discover -s tests \
  -p 'test_plugin.py' -v

# 2. Нативная интеграция: настоящие GoalManager и SessionDB, временный HERMES_HOME
#    HERMES_DISABLE_LAZY_INSTALLS=1 обязателен: без него чужой HERMES_HOME
#    запускает bootstrap зависимостей и перезапускает интерпретатор.
HERMES_DISABLE_LAZY_INSTALLS=1 HERMES_HOME=$(mktemp -d) \
  PYTHONPATH=/Users/somovis/.hermes/hermes-agent \
  /Users/somovis/.hermes/hermes-agent/venv/bin/python -B -m unittest discover -s tests \
  -p 'test_desktop.py' -v

# 3. Пакетные проверки (без установки)
hermes plugins validate . --json
hermes plugins doctor --ci .
hermes skillcraft check skills/goal-workflow
```

`test_plugin.py` использует записывающий fake-context, а не настоящий
PluginManager; реальную регистрацию проверяет `plugins doctor`, а форму манифеста —
`plugins validate`.

## Что НЕ проверено

- Модельный E2E: что LLM сама вызывает инструмент и что штатный judge закрывает Goal.
- Discovery/применение скилла новой сессией.
- Другие поверхности (CLI, Telegram, ACP, Kanban-воркеры) — заявлена Desktop-сессия.
- Гонка «пользовательская пауза ровно в момент update» на настоящем UI: юнит-тест
  проверяет сверку поколения, но не межпроцессную блокировку.

## Структура

```text
plugin.yaml                     манифест (provides_tools: [goal_control])
__init__.py                     from .src.plugin import register
src/contract.py                 валидация контракта и TODO-привязки (без Hermes)
src/service.py                  политика действий, сверка readback (без Hermes)
src/schema.py                   схема инструмента
src/desktop.py                  host-адаптер: session context, GoalManager, транскрипт TODO
src/plugin.py                   register(): 1 tool + 1 bundled skill
skills/goal-workflow/SKILL.md   поведение агента
tests/                          contract, service, plugin, desktop (native)
```

Лицензия: MIT ([LICENSE](LICENSE)).
