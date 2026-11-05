# Ручная установка

Всё на этой странице необязательно. Мастер из [Быстрого старта](../README.ru.md#быстрый-старт) (`python3 setup.py`) покрывает обычный случай; здесь те же шаги вручную, флаги `deploy/setup.sh`, публикация мини-аппа / PWA, режим только SDK и групповой помощник на другой модели.

Английская версия: [manual-install.md](manual-install.md). Назад в [README](../README.ru.md).

## Быстрая установка (без своего домена)

На Linux с пользовательской сессией systemd выполните из скачанного репозитория:

```bash
bash deploy/setup.sh
```

Скрипт запросит токен от @BotFather и разрешённые Telegram id, установит пользовательскую службу и по умолчанию
включит **Tailscale Funnel**: бесплатный постоянный HTTPS-адрес `*.ts.net`, без покупки домена. Сначала установите
Tailscale и войдите в аккаунт (скрипт выведет официальную команду установки, но сам её не запускает). Если вход не
выполнен, скрипт предложит запустить `sudo tailscale up`. Если Funnel в вашей tailnet ещё не включён, скрипт крупно
покажет ссылку включения, откроет её в браузере (при наличии графической сессии), выведет QR-код (если установлен
`qrencode`) для открытия с телефона и **подождёт**, пока вы нажмёте одну кнопку на странице; дальше установка
продолжится сама. Ожидание ограничено `CC_SETUP_FUNNEL_ENABLE_WAIT` секунд (по умолчанию 600); Ctrl+C и повтор
с `--reconfigure-publish --publish=funnel` возможны в любой момент. В `--non-interactive` скрипт лишь печатает
ссылку и завершается, если не добавить `--wait-for-enable`. Адрес сохраняется при перезапусках, пока не меняется DNS-имя машины в Tailscale.
Постоянный адрес нужен установленному PWA, push-подпискам и кнопке Telegram WebApp. Машина должна оставаться в сети.
Подробнее — в [документации Funnel](https://tailscale.com/docs/reference/tailscale-cli/funnel).

- `--dry-run` показывает будущие изменения; `--help` перечисляет параметры. Существующую установку скрипт не
  меняет без `--reconfigure-publish --publish=MODE`: этот режим меняет только публикацию.
- `--non-interactive --token "$BOT_TOKEN" --users 123456789 --publish=funnel` запускает установку без вопросов.
  По возможности вводите токен в скрытом приглашении: аргументы команд видны в списке процессов и истории shell.
- Полностью без кликов: `--ts-api-key-file /absolute/path/tailscale.env` (**файл с правами 600**, строка
  `TS_API_KEY=tskey-api-...` или просто ключ). Создайте ключ в консоли Tailscale: Settings -> Keys -> «Generate access
  token» (API access token) и удалите его после установки: нужен только на время установки. Скрипт читает ключ внутри
  процесса (не в командной строке и не в выводе) и использует официальный [API Tailscale](https://tailscale.com/api):
  читает политику (`GET /api/v2/tailnet/-/acl`, HuJSON), добавляет
  `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]`, если такого ещё нет (комментарии сохраняются;
  запись с `If-Match` из `ETag`), и включает HTTPS-сертификаты (`PATCH /api/v2/tailnet/-/settings`,
  `{"httpsEnabled": true}`). Если API отказал на каком-то шаге, скрипт возвращается к ссылке в один клик. В tailnet
  меняются только эти две настройки; откат: удалите добавленную запись `nodeAttrs` в редакторе Access controls и
  выключите HTTPS-сертификаты на странице DNS. `tailscale funnel reset` прекращает публикацию на машине. Публичный
  DNS-адрес после первого включения может появиться через несколько минут.
- `--with-sdk --sdk-key-file /absolute/path/anthropic.env` добавляет SDK-сессии; `--with-stt` — локальное
  распознавание речи (нужны `ffmpeg`/`ffprobe`). `--stt-groq-key-file /absolute/path/groq.env` добавляет резервный Groq.
  Сначала запишите `ANTHROPIC_API_KEY=value` или `GROQ_API_KEY=value` в **файл с правами 600**; передавайте только путь.
- `--publish=quick` использует установленный `cloudflared` без аккаунта, но **адрес меняется при каждом перезапуске**:
  установленное PWA и push-подписки перестают работать. Это вариант для временного доступа.
- `--publish=cloudflare --cf-hostname app.example.com` выводит команды ручной настройки именованного туннеля для
  вашего домена в Cloudflare. `--cf-api-token-file` принимается для планирования, но файл не читается и не используется:
  инструкция предполагает вход через браузер. `--publish=none` оставляет только Telegram.

Отправьте `/start` для открытия мини-аппа, затем `/pwa` для установки и входа. Funnel записывает URL в файл, который
бот читает без перезапуска. Для смены публикации нужны незаданный `CC_WEBAPP_URL`, стандартный файл URL и порт.
Для push дополнительно нужны `cryptography` и `CC_VAPID_SUB` (см. ниже). Команды удаления скрипт выведет в конце.
## Установка

1. **Создайте бота.** В @BotFather: `/newbot`, скопируйте токен. Включите темы в личных чатах («Threaded Mode» в
   настройках бота), если нужны топики под сессии; без них бот работает с одной «текущей сессией».
2. **Скачайте код.** `git clone https://github.com/zidos-dev/pager ~/zidos-pager` (путь любой, поправьте юнит).
3. **Настройте.**
   ```bash
   mkdir -p ~/.config/zidos-pager
   cp ~/zidos-pager/env.example ~/.config/zidos-pager/env
   chmod 600 ~/.config/zidos-pager/env
   $EDITOR ~/.config/zidos-pager/env     # TG_BOT_TOKEN, TG_ALLOWED_USERS (ваш Telegram id), CC_LANG
   ```
   Числовой id узнайте у любого бота «get my id». **Без `TG_ALLOWED_USERS` бот не запускается.**
4. **Запустите как пользовательскую службу systemd.**
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ~/zidos-pager/deploy/zidos-pager.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now zidos-pager
   journalctl --user -u zidos-pager -f            # в журнале нет текстов сообщений
   loginctl enable-linger "$USER"              # чтобы работало и без входа в систему
   ```
   Откройте бота в Telegram и отправьте `/start`: появится пульт (в режиме тем — в топике «🎛 Агенты»).
5. **Мини-апп / PWA (по желанию).** Бот отдаёт мини-апп на `127.0.0.1:18795`; Telegram и телефонам нужен публичный
   https-адрес перед ним. Варианты:
   - **Именованный туннель Cloudflare** (постоянный адрес): по образцу `deploy/zidos-pager.yml.example`, установите
     `deploy/zidos-pager-tunnel.service`, в env задайте `CC_WEBAPP_URL=https://cc.example.com`.
   - **Быстрый туннель** (случайный адрес `*.trycloudflare.com`, меняется при перезапуске): установите
     `deploy/zidos-pager-quick-tunnel.service`; бот читает адрес из файла и обновляет кнопку меню в Telegram.
   - Подойдёт и любой обратный прокси с TLS — укажите его адрес в `CC_WEBAPP_URL`.
6. **Установка PWA (по желанию).** Отправьте боту `/pwa`, откройте адрес в Safari/Chrome, «На экран Домой», запустите
   приложение с экрана и вставьте одноразовый код (у PWA на iPhone отдельное хранилище, поэтому ссылка из Telegram не
   переносит вход). `/devices` — список и отзыв устройств.
7. **Push (по желанию).** `pip install cryptography`, задайте `CC_VAPID_SUB=mailto:you@example.com`, перезапустите.
8. **Файлы из сессий (по желанию).** `ln -s ~/zidos-pager/deploy/cc-send ~/.local/bin/cc-send`; сессия сможет вызывать
   `cc-send report.pdf "подпись"`.

Тесты: `python3 -m unittest discover -s tests` (без сети, Claude и tmux).
## Без Claude Code (только SDK)

Бот может работать на машине вообще без CLI `claude`. Тогда используются только сессии SDK-раннера: панель их
показывает, «🆕 Новая сессия» запускает сессию в раннере, а квота подписки, экран tmux и автоимена скрыты. Если нет ни
`claude`, ни раннера с ключом, панель сообщает об этом. `CC_CLAUDE=off` включает этот режим даже при установленном
`claude`.

**Нужно:** Python 3.10+ (для бота 3.12+), API-ключ Anthropic и токен Telegram-бота. Колесо `claude-agent-sdk`
приносит собственный CLI, отдельно Claude Code ставить не нужно.

1. Установите раннер в отдельное виртуальное окружение:
   ```
   cd ~/zidos-pager
   python3 -m venv .venv-sdk && .venv-sdk/bin/pip install claude-agent-sdk
   ```
2. Положите ключ в файл со строкой `NAME=value` и закройте его: `chmod 600 ~/.config/zidos-pager/anthropic.env`
   (например, `ANTHROPIC_API_KEY=sk-ant-...`).
3. Создайте `~/.config/zidos-pager/sdk.env` (`chmod 600`) только с настройками раннера — не env-файл бота, в нём токен
   Telegram:
   ```
   CC_SDK_KEY_FILE=/home/you/.config/zidos-pager/anthropic.env
   CC_SDK_KEY_VAR=ANTHROPIC_API_KEY
   CC_SDK_MODEL=sonnet
   CC_SDK_BUDGET=1.00
   ```
4. Установите оба юнита (поправьте пути внутри) и запустите:
   ```
   mkdir -p ~/.config/systemd/user
   cp deploy/zidos-pager.service deploy/zidos-pager-sdk.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now zidos-pager-sdk zidos-pager
   ```

**Работает:** сессии, запущенные из бота, переписка с ними, запросы разрешений 🔐, кнопки вопросов, стоимость 💲 и
бюджет, топики, уведомления и мини-апп (история, файлы). **Нет** в этом режиме: сессий вашей подписки Claude, экрана
и клавиш tmux, квоты подписки, автоимён.

## Свой бот на другой модели (DeepSeek, OpenRouter и другие)

SDK-раннер работает с любым провайдером, у которого есть Anthropic-совместимый адрес. Бот в Telegram и ключ у вас
свои, модель выбирается тремя настройками.

**Нужно:** Linux с `python3`, `python3-venv`, `bubblewrap` и `socat` (`sudo apt install python3-venv bubblewrap socat`): без песочницы ОС сессии
чата не запускаются.

1. Создайте бота в @BotFather и узнайте свой числовой id в Telegram через любого бота вида «get my id». Скачайте код:
   `git clone https://github.com/zidos-dev/pager ~/zidos-pager && cd ~/zidos-pager` или из архива:
   `tar -xzf zidos-pager-own-bot.tar.gz -C ~ && cd ~/zidos-pager`
2. Положите ключ в файл и закройте доступ:
   ```
   mkdir -p ~/.config/zidos-pager
   echo 'DEEPSEEK_API_KEY=sk-...' > ~/.config/zidos-pager/deepseek.env
   chmod 600 ~/.config/zidos-pager/deepseek.env
   ```
3. Установите:
   ```
   deploy/setup.sh --token 'ТОКЕН_БОТА' --users ВАШ_ID --publish none --with-sdk \
     --sdk-key-file ~/.config/zidos-pager/deepseek.env --sdk-key-var DEEPSEEK_API_KEY \
     --sdk-base-url https://api.deepseek.com/anthropic --sdk-model deepseek-flash
   ```
   Теперь с моделью можно говорить в личке с ботом.
4. **Чтобы бот отвечал в группе:** добавьте его туда и напишите `@ваш_бот id` (номер группы придёт вам в личку).
   Затем допишите в `~/.config/zidos-pager/env` и выполните `systemctl --user restart zidos-pager`:
   ```
   CC_RESPONDER_CHAT=-100...                 # номер группы
   CC_RESPONDER_CWD=/home/you/knowledge      # папка, по которой бот отвечает: обычная или клон git
   CC_RESPONDER_NAME=Помощник                # как бот называет себя
   CC_RESPONDER_BRIEF=/home/you/brief.md     # необязательно: роль, тон, правила
   ```
   Сначала выключите приватный режим: отправьте @BotFather `/setprivacy`, выберите Disable и добавьте бота в группу заново.
   С включённым приватным режимом Telegram в нашей проверке не доставлял боту обычные «@бот вопрос», только команды
   вида `/ask@bot вопрос`; пока режим не выключен, мини-апп показывает это как ошибку бота.
   Бот отвечает на упоминания и реплаи на свои сообщения. Изменение существующих файлов и всё, что выходит из песочницы, ждёт вашего 🔐; новый файл в папке может появиться и без него.

| Провайдер | `--sdk-base-url` | `--sdk-model` | Переменная ключа |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/anthropic` | `deepseek-flash` или `deepseek-v4-pro` | `DEEPSEEK_API_KEY` |
| OpenRouter | `https://openrouter.ai/api` | например, `deepseek/deepseek-v4.1-flash` | `OPENROUTER_API_KEY` |
| GLM (z.ai) | `https://api.z.ai/api/anthropic` | `glm-5.3` | `GLM_API_KEY` |
| Kimi (не проверено: нужна подписка Kimi Code) | `https://api.kimi.com/coding` | `kimi-for-coding` | `KIMI_API_KEY` |

В `sdk.env` раннера есть ещё `CC_SDK_STRONG_MODEL` (модель для просьб что-то изменить, по умолчанию та же),
`CC_SDK_MAX_TURNS` (шагов на ответ, по умолчанию 30) и `CC_CHAT_SITE` (сайт, доступный из песочницы чата).

**Codex** не Anthropic-совместим, поэтому для группы это отдельный движок: выполните `codex login` и задайте в `~/.config/zidos-pager/env` `CC_RESPONDER_ENGINE=codex` (и при желании `CC_CODEX_MODEL`). Он читает папку и отвечает, но ничего не меняет; SDK-раннер для группы не нужен. Сессии в личке остаются на раннере. Подходит только для группы своих: модель может прочитать файл входа Codex, и гость может его запросить.

**В мини-аппе** те же настройки открывает кнопка с ползунками рядом с «Сессии»: выберите провайдера, вставьте ключ, нажмите «Проверить и подключить». Ключ сохраняется только после того, как провайдер его принял, и обратно на экран не возвращается. Мини-аппу нужен публичный адрес: при установке вместо `--publish none` укажите `--publish quick`. Подключение перезапускает раннер и обрывает его текущие сессии.

**Важно знать.** Бюджет 💲 здесь не действует: SDK знает только цены Claude, поэтому расход ограничен балансом у
провайдера. Когда баланс кончится, группа получит нейтральное сообщение, а текст ошибки придёт вам. Поиск в интернете
и открытие страниц зависят от провайдера: у DeepSeek в документации заявлен `web_search`, про `web_fetch` не сказано. Не держите в папке секреты: любой участник группы может спросить о её содержимом. Файлы `.env` закрыты, секрет под другим именем нет.

## Ещё боты из командной строки

Каждый дополнительный бот для группы — отдельная пара процессов (`zidos-pager-bot@<бот>`, `zidos-pager-bot-sdk@<бот>`; на Mac
`dev.zidos.pager.bot.<бот>`, `dev.zidos.pager.sdk.<бот>`), настройки — в `~/.config/zidos-pager/bots/<бот>/`. Мастер и лист «Боты»
в мини-аппе вызывают те же команды: каждая читает JSON на входе и печатает JSON (`{"ok": false, "error": ...}` при отказе).
Ключи не вводите в командной строке: подавайте их из файла.

```
cd ~/zidos-pager
echo '{"token": "123456:ABC..."}' | python3 zidos_pager.py bots add            # -> {"ok": true, "slug": "my_bot", ...}
python3 - <<'PY' | python3 zidos_pager.py bots save                              # модель и ключ, проверяются до сохранения
import json; print(json.dumps({"slug": "my_bot", "provider": "deepseek", "key": open("deepseek.key").read().strip()}))
PY
echo '{"slug": "my_bot", "about": "Школа танцев: расписание, цены, запись."}' | python3 zidos_pager.py bots persona
echo '{"slug": "my_bot", "cwd": "~/dance", "site": "https://dance.example", "chat": -100123}' | python3 zidos_pager.py bots save
python3 zidos_pager.py bots list
echo '{"slug": "my_bot"}' | python3 zidos_pager.py bots remove
```

`bots persona` возвращает сгенерированные `brief` и `persona` (`name`, `short`, `description`), но не сохраняет их: передайте
их в `bots save` как есть или поправив. Группы, которые бот видел, появляются в `bots list` (`groups`). Провайдеры:
`deepseek`, `glm`, `kimi`, `openrouter`, `anthropic`, `custom` (с `base`), `codex`.
