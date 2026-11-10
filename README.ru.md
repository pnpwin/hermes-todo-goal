# hermes-todo-goal

[English](README.md) · **Русский**

Инструмент `goal_control`: LLM управляет Goal **текущего чата** без ручного `/goal` —
создание, чтение, изменение, пауза, продолжение и запрос проверенного завершения.
TODO необязателен.

**Статус: локальный невыпущенный кандидат hardening. Эти изменения не установлены.**
Общий native CAS для UI/judge/плагина остаётся внешней зависимостью.
Автоматическая активация не квалифицирована; публикация и установка требуют отдельного разрешения.

## Исторические проверки (не приёмка текущего кандидата)

Ниже описан плагин `9d278b2` (runtime без изменений в `c42dd1c`, 0.1.1),
а не текущие изменения. Нового модельного E2E/продолжения для кандидата нет.

- Живой прогон моделью (CLI `chat --cli`, gpt-6.1-sol): агент сам нашёл встроенный
  скилл `goal-workflow`, вызвал `goal_control` `status` → `create` → создал артефакт →
  `status` снова, и вернул ту же revision, что посчитал плагин; Goal лёг в
  `state_meta` под ключом `goal:<session_key>`.
- Путь judge: `GoalManager.evaluate_after_turn()` на этом ответе (ровно тот вызов,
  который делает Desktop на границе хода) вернул `verdict: done` с конкретной
  причиной и перевёл состояние `active → done`, `turns_used 1/30`.
- `pause` → `resume` через инструмент, revision сходятся, бюджет не тронут.
- Проверенная граница: идентичный `create` на активном Goal — no-op, а не ошибка.

Установка, включение в профиль, перезапуск и модельный E2E требуют отдельного
разрешения. Этот каталог сам по себе ничего не активирует.

### Ограничение поверхности

Продолжение зависит от процесса, исполняющего ход. Старый локальный REPL и
CLI через gateway могут иметь разные пути. Новый `-z` не доказывает новый gateway
или загрузку нового плагина. Прямой вызов `GoalManager.evaluate_after_turn()`
не подтверждает автоматическое выполнение следующего хода.

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
| `update` | `contract`, `expected_revision` | только идентичный контракт (read-only no-op); замена требует native user control |
| `pause` | `expected_revision`, `reason` | остановить автопродолжение (не прерывает текущий вызов) |
| `resume` | `expected_revision`, `reason` | снять только непустую `agent:`-паузу при остатке бюджета и без wait barrier |
| `complete` | `expected_revision`, `evidence` | запросить проверку; возвращает `verification_status: pending` |

Полная семантика и коды ошибок: [skills/goal-workflow/references/tool-contract.md](skills/goal-workflow/references/tool-contract.md).

## Границы (сознательные)

- `create` не заменяет живой Goal: другой контракт при `active`/`paused` →
  `goal_exists`. Тот же контракт при `active` — идемпотентный no-op
  (`changed: false`); завершённый (`done`) Goal можно заменить новым.
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

### Полномочия, аудит и незакрытая атомарность

- Изменённый контракт в `update` → `contract_change_requires_user`. Доверенного
  in-tool approval нет; `reason`/`approved` не являются разрешением. Нельзя обходить
  отказ удалением/пересозданием живой цели.
- Модель не снимает пользовательские, неизвестные, gate/budget-паузы. Префикс
  `agent:` обозначает владельца паузы, но не доказывает ответ пользователя.
- TODO читается как последний точно спаренный persisted result по доверенному
  transcript ID. Это **не live TodoStore**; route key Goal и ID транскрипта различны.
- Audit v1: `plugin-data/hermes-todo-goal/goals/<sha256[:32]>.json`. Binding привязан
  к native `created_at` и не исчезает после вытеснения 50 событий. Legacy create
  восстанавливается только для совпадающего поколения, без миграции при чтении.
  Если originating legacy create вытеснен, binding остаётся unknown/missing:
  non-create запись отказывает, а не закрепляет неизвестное как confirmed null.
  Повреждённый журнал сохраняется. Evidence (до 4000 символов) хранится как данные
  задачи без автоматического удаления секретов; секреты передавать нельзя.
- Запись использует уникальный временный файл и directory lock. Конфликт блокировки
  даёт явный отказ; stale lock восстанавливает оператор, не агент автоматически.
  Atomic replace не является общей транзакцией native+audit или гарантией при потере питания.
- При native success и audit failure: `ok:false`, `outcome:partial`, актуальные
  state/revision и `audit_status:failed`. Неудачный readback → `unknown`, nullable
  state/revision/changed. Прочитать status, сообщить неопределённость; **не повторять автоматически**.
- Hash revision/readback не закрывают гонки с UI/judge и ABA. Полная автоматическая
  активация заблокирована до общего native CAS.

### Автомаршрутизация остаётся выключенной

`plugins.entries.hermes-todo-goal.settings.auto_create` по умолчанию false.
Нужны literal boolean true и квалифицированный общий native CAS. В этом кандидате
capability намеренно unavailable: даже opt-in не добавит секцию на текущем host.
Положительный unit-тест подставляет capability stub, а не доказывает delivery/E2E.
Политика: прямой запрос на проверяемый результат, которому вероятно нужны несколько
ходов, а не просто несколько tool calls. Существующая цель не переписывается автоматически.

## Установка опубликованного релиза (не разрешение установить кандидата)

Опубликованные релизы: https://github.com/pnpwin/hermes-todo-goal (MIT).
Команды ниже устанавливают опубликованный репозиторий, а не публикуют локальные
изменения. Исторический CI не подтверждает текущий candidate.

Голый путь в `hermes plugins install` не принимается: он разбирается как GitHub
`owner/repo` (`_resolve_git_url` даёт `https://github.com/Users/somovis.git` + subdir).
Три рабочих варианта:

**A. Управляемая установка из опубликованного репозитория** (запись provenance в
`plugins/.install-metadata.json`, работают `plugins update`):

```sh
hermes plugins install pnpwin/hermes-todo-goal --enable
```

**B. Управляемая установка из локального каталога** — тот же provenance, через
`file://`:

```sh
cd /path/to/hermes-todo-goal
hermes plugins install "file://$PWD" --enable
```

**C. Ручная директория** (без provenance и без авто-обновлений — так на этой машине
живут `hermes-skillcraft`, `ast-index`):

```sh
cp -R /path/to/hermes-todo-goal ~/.hermes/plugins/
hermes plugins enable hermes-todo-goal
```

После загрузки инструмент появляется как `goal_control` в toolset
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
# 1. Чистое ядро: контракт, политика, схема, регистрация (только stdlib)
python3 -B -m unittest discover -s tests -p 'test_contract.py' -v
python3 -B -m unittest discover -s tests -p 'test_service.py' -v
python3 -B -m unittest discover -s tests -p 'test_plugin.py' -v
python3 -B -m unittest discover -s tests -p 'test_todo_pairing.py' -v
python3 -B -m unittest discover -s tests -p 'test_audit.py' -v

# 2. Нативная интеграция: настоящие GoalManager и SessionDB, временный HERMES_HOME.
#    HERMES_DISABLE_LAZY_INSTALLS=1 обязателен: без него чужой HERMES_HOME
#    запускает bootstrap зависимостей и перезапускает интерпретатор.
HERMES_DISABLE_LAZY_INSTALLS=1 HERMES_HOME=$(mktemp -d) \
  PYTHONPATH="$HERMES_AGENT_DIR" \
  "$HERMES_AGENT_DIR/venv/bin/python" -B -m unittest discover -s tests \
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

- Поверхность Desktop/TUI end-to-end: toolset там включён, вызов judge прогнан напрямую,
  но ни один ход Desktop ещё не создал и не закрыл Goal.
- Реальная постановка следующего хода продолжением в живой сессии (проверен только путь
  принятия решения).
- Другие поверхности (Telegram, ACP, Kanban-воркеры) — не прогонялись.
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

Лицензия: MIT ([LICENSE](LICENSE)). Изменения: [CHANGELOG.md](CHANGELOG.md).
