# Проверки мини-аппа (Playwright, Chromium + WebKit)

Стенд `stand_server.py` отдаёт статику `assets/webapp` и демо-API на вымышленных данных `tests/stand/demo2.json`. Бэкенд, Telegram и tmux не нужны.

Нужно: `pip install playwright && playwright install chromium webkit`.

```
cd tests/webapp
PORT=19301 python3 check_defects.py      # D1–D5: пилюля, ошибка списка, Esc, ширина листов, обновление квоты
PORT=19301 python3 check_perm.py         # лист 🔐 разрешений
PORT=19301 python3 check_perm_rows.py    # группа «Нужно разрешение» наверху списка
PORT=19317 python3 check_perms.py        # лист «Права», права бота, «Везде» в запросе, строки правил в ленте (PW_BROWSER=webkit)
PORT=19301 python3 check_own_answer.py   # «Свой ответ» в листе вопроса
PORT=19301 python3 check_multi.py        # множественный выбор в листе вопроса
PORT=19301 python3 check_sheet_steps.py  # лист стоит между шагами многовопросного вопроса (1→2→review)
PORT=19301 python3 check_back_order.py  # назад со слоя: «Запустить», «+» и капсула — вместе с уходом слоя; жест назад не смахивает строку
PORT=19301 python3 check_header_menu.py  # закреплённая шапка, меню «⋯», автообновление
PORT=19301 python3 check_capsule.py      # капсула фильтров
PORT=19301 python3 check_kb_rule.py      # список скрыт под слоем, пока поле в фокусе
PORT=19301 python3 regress_open.py       # раскрытие/закрытие сессии не оставляет следов на карточках
PORT=19301 python3 check_ios_back.py     # iPhone: назад ведёт само приложение (кнопка Telegram скрыта), Android — кнопка Telegram
PORT=19302 python3 check_pwa_history.py  # iPhone PWA: слои не пишут историю (свайп от края — наш морф), в браузере «назад» по истории
PORT=19303 python3 check_ask_fit.py      # лист вопроса со «своим ответом» и клавиатурой: карточки вариантов целиком, список прокручивается
PORT=19304 python3 check_chain_wrap.py   # плашка «Продолжение №k · предыдущая …»: «›» не уходит одна на новую строку
PORT=19305 python3 check_owner_files.py  # файлы владельца в ленте — превью/альбом, путь с пробелами и скобками
PORT=19301 python3 check_attach.py       # файлы прикрепляются к черновику, уходят одним /upload с подписью
PORT=19301 python3 check_nav_one.py      # сессия открывается/закрывается одним морфом, без слайда продукта под ним
PORT=19301 python3 check_compose_low.py  # поле сессии у низа на iPhone (safe area один раз), с клавиатурой без строки квоты
PORT=19301 python3 check_list_keep.py    # выход из сессии (и тап по полю перед выходом) не прокручивает список
node check_header_lifecycle.cjs; node check_own_answer_state.cjs   # без браузера
```

- `APP_DIR` — другой checkout статики (по умолчанию `assets/webapp` этого репо); `BEFORE_APP_DIR` — база для снимков «до» в `check_capsule.py`.
- Запускать по одной: параллельные прогоны делят машину и дают таймауты. Порт проверять `ss -ltnp`.
- Под нагрузкой WebKit и сценарии «поздний ответ» флакают (header_menu 94–96/96, own_answer 133–136/136): упавшее перед выводом повторить.
- `check_defects` D5 в десктопном WebKit пропускает свайп (нет Touch) — это SKIP, а не PASS.
- Снимки (`*.png`) пишутся рядом и не коммитятся.
- `check_spacing.py` — one 16 px screen edge for every block (titles, tiles, cards, feed, composer, question sheet) and one inner padding for tiles, cards and sheet options; 390/360, chromium+webkit (owner 27.10).
