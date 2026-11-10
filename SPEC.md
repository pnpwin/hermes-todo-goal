# hermes-todo-goal — спецификация локального hardening

Статус: локальный невыпущенный кандидат; изменения не установлены.
Исходная версия 0.1.1, BASE c42dd1c5a8ee9d523d9eeefa7b0c0c0181c731d2.
Реализация плагина и изолированные проверки разрешены; изменение установленного
ядра, профилей, публикация и активация не входят в этот этап.

## Архитектура

Один `goal_control`, toolset `goals`, поверх native GoalManager. Нет второго
GoalStore, continuation loop, собственного judge или прямых SQLite-записей.
TODO — изменяемый план; Goal — контракт результата. Галочки не являются evidence.
Контракт: outcome, verification, constraints, boundaries, stop_when — пять
непустых полей, максимум 4000 символов каждое, лишние поля запрещены.

## Полномочия и lifecycle

- `status` читает native state и audit binding; не создаёт/исправляет audit-файлы.
- `create` требует полного отсутствия native record. Done/cleared также блокируют
  замену. Идентичный active create — read-only no-op. Unreadable/corrupt native record
  даёт state_unreadable; недоступный store не считается отсутствующим Goal.
- `update`/`pause`/`resume` → action_disabled до обращения к хосту. Их native mutation
  paths удалены. Контракт, паузы, бюджет и lifecycle управляются native user control.
  Ни reason, ни approved=true не дают полномочий; обход через clear/create запрещён.
- `complete` только для active, сохраняет evidence (до 4000 символов) и возвращает
  pending. Не вызывает judge и не ставит done. Evidence повторяется в ответе;
  native judge и gates остаются владельцами завершения.
- Модель не задаёт session_id/profile/home/db_path/approved/force/reset_budget/gates.
  Обязательная expected_revision для complete — fingerprint
  полного native snapshot, НЕ атомарная ревизия и НЕ защита от ABA.

## Идентичность и TODO

Goal использует bound_session_key() без env fallback. Делегированный child
отвергается даже при унаследованном родительском route key. Transcript session_id
поступает отдельно из trusted registry dispatch; conversation_id не заменяет его.
Без trusted transcript ID binding отказывает, standalone Goal остаётся доступен.

По expected_todo_revision читается последний paired persisted result своей сессии,
лимит 400 сообщений с ancestors. Это НЕ live TodoStore. Pairing: непустой точный
call ID, ближайший assistant, остановка на user/system; host predicate проверяет
legacy alias/deferred bridge. Непарные строки игнорируются. Последний парный clear
или invalid payload не позволяет восстановить старый план. Пустой/неактивный план
не привязывается; Goal без TODO разрешён.

## Аудит и неопределённость

`plugin_data_dir` владеет write path:
`plugin-data/hermes-todo-goal/goals/<sha256(route_key)[:32]>.json`.
Host helper создаёт каталог; read path вычисляет тот же документированный путь
через текущий get_hermes_home(), не вызывая создающий helper.

Schema v1: native created_at generation, binding отдельно от последних 50 events.
Legacy binding читается только из matching create; чтение не мигрирует файл.
Если originating create отсутствует, binding unknown/missing; non-create audit
отказывает, сохраняя старые bytes, вместо подтверждения отсутствующей привязки.
Новое поколение сбрасывает binding. Повреждённый/неподдерживаемый файл сохраняется
и даёт failed. Evidence — task data без автоматического удаления секретов.

Запись: per-file directory lock, unique same-directory temp, flush/fsync и replace.
Конфликт lock — явный отказ; stale lock требует operator recovery. Нет общей
транзакции native+audit и гарантии directory durability при потере питания.

Native success + audit failure → ok:false, partial, фактический state/revision.
Неудачный readback → unknown, changed:null; state/revision null если прочитать
невозможно. Никакого rollback или auto-retry. Status может вернуть ok:true для
native state и audit_status:failed/warning/null binding. Аудит не GoalStore.

## Offline routing policy

`settings.auto_create` — bool, default false. Literal true регистрирует секцию на
совместимом host. CAS dependency снята по выбору пользователя: это plugin-only
create-once, без права изменять ядро или runtime-конфиги. Policy согласована с schema
и skill: direct verifiable long-running work, не вопросы/планы/короткие задачи.

## Граница полной приёмки

Единственная native-мутация — создание при отсутствии записи. Последний preflight
не атомарен с native UI writer: одновременное создание всё ещё может конфликтовать.
Readback и audit lock этого не устраняют. Изолированные прогоны требуют отсутствия
конкурентного UI управления Goal. Нельзя выдавать их за all-writer/ABA safety.
Старый companion-CAS план superseded; изменение ядра пользователем запрещено.

Текущие offline suites: contract, service, create_once, plugin, todo_pairing, audit; native
suite: test_desktop.py, реальный dispatch/GoalManager/SessionDB на synthetic homes.
CI перечисляет только pure suites; локальный запуск не означает remote CI.
Модельный E2E, delivery полного prompt, live requeue/consumed continuation и A/B
требуют отдельных gate receipts; исторический CLI-прогон их не заменяет.

Полная API-справка: [tool-contract](skills/goal-workflow/references/tool-contract.md).
