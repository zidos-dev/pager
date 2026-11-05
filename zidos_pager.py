#!/usr/bin/env python3
"""Zidos Pager: a Telegram bot for Claude Code sessions (an "agent view" for your phone).

Standard library only (cryptography is optional, for Web Push). Config: ~/.config/zidos-pager/env (KEY=VALUE).
"""
import base64
import email.parser
import email.policy
import fnmatch
import hashlib
import hmac
import json
import mimetypes
import os
import platform
import secrets
import shutil
import socket
import struct
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # Web Push (RFC 8291/8292) — the only dependency outside the stdlib; without it push is simply disabled
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    HAVE_CRYPTO = True
except ImportError:  # pragma: no cover
    HAVE_CRYPTO = False

# ---------------------------------------------------------------- i18n
# Source strings are English; STR["ru"] maps them to Russian. The language comes from CC_LANG (en|ru).
LANGS = ("en", "ru")
LANG = "en"
_RU = {
    "\n"
    "\n"
    "…full text in the attachment.":
        "\n"
        "\n"
        "…полный текст — во вложении.",
    "\n"
    "⚠️ data is stale: it is updated while any Claude Code is open":
        "\n"
        "⚠️ данные устарели: обновляются, пока открыт любой Claude Code",
    " · data %s": " · данные %s",
    " ⚠️ data is stale": " ⚠️ данные устарели",
    "%.1f MB": "%.1f МБ",
    "%d B": "%d Б",
    "%d KB": "%d КБ",
    "%d d": "%d дн",
    "%d d ago": "%d дн назад",
    "%d h": "%d ч",
    "%d h ago": "%d ч назад",
    "%d min": "%d мин",
    "%d min ago": "%d мин назад",
    "%s\n"
    "→ sent: %s":
        "%s\n"
        "→ отправлено: %s",
    "%s limit": "лимит %s",
    "%s — no data": "%s — нет данных",
    "(empty)": "(пусто)",
    "(screen is empty)": "(экран пуст)",
    ", resets at %s": ", сброс в %s",
    "5 h": "5 ч",
    "5 hours": "5 часов",
    "7 d": "7 дн",
    "7 days": "7 дней",
    ": request rejected by safeguards": ": защита отклонила запрос",
    "<i>Write a task here — I'll start a new session.</i>":
        "<i>Напишите задачу сюда — запущу новую сессию.</i>",
    "<i>was: %s</i>": "<i>было: %s</i>",
    "A command from the list, on one line, up to 200 characters":
        "Команда из списка, в одну строку и не длиннее 200 символов",
    "API error": "ошибка API",
    "Advisor": "Советник",
    "After the path, write what needs to be done.": "После пути напишите, что нужно сделать.",
    "All names are already clear.": "Все имена уже понятные.",
    "Also changes the default for new sessions": "Меняет и умолчание для новых сессий",
    "App devices:": "Устройства приложения:",
    "App on your phone": "Приложение на телефон",
    "Applying names: %d": "Применяю имена: %d",
    "Archive done": "Готовое в архив",
    "Archived": "В архиве",
    "Archived: %d": "В архив: %d",
    "Cancel": "Отмена",
    "Cancelled.": "Отменено.",
    "Choose a folder:": "Выберите папку:",
    "Claude quota": "Квота Claude",
    "Claude quota\n"
    "No data yet: file not found. It is updated while any Claude Code is open.":
        "Квота Claude\n"
        "Данных пока нет: файл не найден. Он обновляется, пока открыт любой Claude "
        "Code.",
    "Clean ✨": "Чисто ✨",
    "Clear context": "Очистить контекст",
    "Close the topic «%s»? The session keeps running.": "Закрыть топик «%s»? Сессия продолжит работать.",
    "Compact context": "Сжать контекст",
    "Context": "Контекст",
    "Could not create the session": "Не получилось создать сессию",
    "Could not create the session. Check that Claude Code starts.":
        "Не получилось создать сессию. Проверьте, что Claude Code запускается.",
    "📨 Send": "📨 Отправить",
    "Sent": "Отправлено",
    "🎙 %s · ✔ sent": "🎙 %s · ✔ отправлено",
    "This voice message is no longer available": "Это голосовое сообщение уже недоступно",
    "Voice input is not set up: start the zidos-pager-stt service (see README)":
        "Голосовой ввод не настроен: запустите службу zidos-pager-stt (см. README)",
    "No speech recognized": "Речь не распознана",
    "Could not recognize the speech": "Не удалось распознать речь",
    "Send the voice message to the topic of the session you need.":
        "Пришлите голосовое сообщение в топик нужной сессии.",
    "Unsupported audio type": "Неподдерживаемый тип аудио",
    "No audio in the request": "В запросе нет аудио",
    "Voice message is larger than 10 MB": "Голосовое сообщение больше 10 МБ",
    "Could not download the file. Try again.": "Не получилось скачать файл. Попробуйте ещё раз.",
    "Could not get the session list. The panel will refresh automatically.":
        "Не удалось получить список сессий. Пульт обновится автоматически.",
    "Could not get the session list. Try again in a couple of seconds.":
        "Не удалось получить список сессий. Попробуйте ещё раз через пару секунд.",
    "Could not open the session: tmux is not responding.": "Не удалось открыть сессию: tmux не отвечает.",
    "Could not pass the text to tmux.": "Не получилось передать текст в tmux.",
    "Could not paste the text into the session.": "Не получилось вставить текст в сессию.",
    "Could not read the file": "Не удалось прочитать файл",
    "Could not save the file": "Не удалось сохранить файл",
    "Could not save the file.": "Не получилось сохранить файл.",
    "Could not send the file: %s": "Не смог отправить файл: %s",
    "Could not send the file: the path is not allowed or the file does not "
    "exist":
        "Не смог отправить файл: путь не разрешён или файла нет",
    "Could not stop the session. Try again.": "Не получилось остановить сессию. Попробуйте ещё раз.",
    "Describe the task": "Опишите задачу",
    "Describe the task in one message.": "Опишите задачу одним сообщением.",
    "Device": "Устройство",
    "Devices of the app": "Устройства приложения",
    "Disconnect: %s": "Отключить: %s",
    "Done": "Готово",
    "Effort": "Усилие",
    "Empty answer": "Пустой ответ",
    "Empty answer.": "Пустой ответ.",
    "Empty message": "Пустое сообщение",
    "Empty name": "Пустое имя",
    "Error: TG_BOT_TOKEN is not set (file ~/.config/zidos-pager/env or "
    "ZIDOS_PAGER_ENV).":
        "Ошибка: TG_BOT_TOKEN не задан (файл ~/.config/zidos-pager/env или "
        "ZIDOS_PAGER_ENV).",
    "Failed: %s": "Не получилось: %s",
    "Fast mode": "Быстрый режим",
    "File from owner: %s (%s, %s)": "Файл от владельца: %s (%s, %s)",
    "File is larger than 50 MB": "Файл больше 50 МБ",
    "Files from owner (%d):\n": "Файлы от владельца (%d):\n",
    "First pick a session: press «📋 Sessions».": "Сначала выберите сессию: нажмите «📋 Сессии».",
    "Folder not found": "Папка не найдена",
    "Folder: ": "Папка: ",
    "Help": "Справка",
    "Key not allowed": "Недопустимая клавиша",
    "Keyboard removed. The panel is the message above.": "Клавиатура убрана. Пульт — сообщение выше.",
    "Last reply": "Последний ответ",
    "Model": "Модель",
    "More ▶": "Ещё ▶",
    "Move %d finished sessions to the archive? Conversations are kept, topics "
    "will be closed.":
        "Убрать в архив готовых сессий: %d. Разговоры сохранятся, топики закроются.",
    "Name updated: «%s»": "Имя обновлено: «%s»",
    "New name for «%s»": "Новое имя для «%s»",
    "New names": "Новые имена",
    "New session": "Новая сессия",
    "Next →": "Далее →",
    "No devices. Add one: /pwa": "Устройств нет. Добавить: /pwa",
    "No file in the request": "Нет файла в запросе",
    "No finished sessions.": "Готовых сессий нет.",
    "No model replies yet.": "Ответов модели пока нет.",
    "No more than %d files at a time": "Не больше %d файлов за раз",
    "No question, or it is already closed": "Вопроса нет или он уже закрыт",
    "No quota data yet: the data file was not found. It is updated while any "
    "Claude Code is open.":
        "Квота пока неизвестна: файл с данными не найден. Он обновляется, пока "
        "открыт любой Claude Code.",
    "No sessions": "Сессий нет",
    "No such option": "Нет такого варианта",
    "No topics of finished sessions.": "Топиков готовых сессий нет.",
    "Not sending an empty message.": "Пустое сообщение не отправляю.",
    "The session does not show an input field. Open 📺 Screen.":
        "Сессия не показывает поле ввода. Откройте 📺 Экран.",
    "Not sure the text reached the answer, so Enter was not pressed. Open 📺 "
    "Screen.":
        "Не уверен, что текст попал в ответ, поэтому Enter не нажат. Откройте 📺 "
        "Экран.",
    "Not sure the text reached the input field, so Enter was not pressed (to "
    "avoid sending the wrong thing). Open 📺 Screen.":
        "Не уверен, что текст попал в поле ввода, поэтому Enter не нажат (чтобы не "
        "отправить лишнего). Откройте 📺 Экран.",
    "Nothing is waiting or running.": "Ничего не ждёт и не работает.",
    "Notifications off": "Уведомления выключены",
    "Notifications on": "Уведомления включены",
    "Open and sign in": "Открыть и войти",
    "Optional: what to keep": "Необязательно: что сохранить",
    "PWA is not configured yet.": "PWA ещё не настроено.",
    "Path is not allowed or the file does not exist": "Путь не разрешён или файла нет",
    "Question %d of %d": "Вопрос %d из %d",
    "Quota · 5 h %s · 7 d %s": "Квота · 5 ч %s · 7 дн %s",
    "Quota: no data": "Квота: нет данных",
    "Remove finished topics": "Убрать готовые топики",
    "Rename": "Переименовать",
    "Result: ": "Итог: ",
    "Review the answers and submit.": "Проверьте ответы и отправьте.",
    "Send the file to a session topic.": "Файл нужно прислать в топик сессии.",
    "Send the file to the topic of the session you need.": "Пришлите файл в топик нужной сессии.",
    "Session list": "Список сессий",
    "Session screen": "Экран сессии",
    "Sessions": "Сессии",
    "Sign out everywhere": "Выйти везде",
    "Start with an empty context? The previous conversation stays on disk.":
        "Начать с пустым контекстом? Прежний разговор останется на диске.",
    "Starting in %s…": "Запускаю в %s…",
    "Status": "Статус",
    "Stop a session": "Остановить сессию",
    "Stop and archive? The conversation is kept.": "Остановить и убрать в архив? Разговор сохранится.",
    "Stop «%s»?": "Остановить «%s»?",
    "Suggested: %d": "Предложено: %d",
    "Superseded": "Сменились",
    "Telegram did not accept the file": "Telegram не принял файл",
    "Telegram error": "ошибка Telegram",
    "Text pasted but Enter did not go through. Open 📺 Screen.":
        "Текст вставлен, но Enter не нажался. Откройте 📺 Экран.",
    "The archive is empty.": "Архив пуст.",
    "The file is larger than 20 MB — send it via the mini-app":
        "Файл больше 20 МБ — пришлите через мини-апп",
    "The input field contains someone else's text; not sending. Open 📺 Screen.":
        "В поле ввода остался чужой текст, не отправляю. Откройте 📺 Экран.",
    "The key was not pressed.": "Клавиша не нажалась.",
    "The name is empty — left as it was.": "Имя пустое — оставил как было.",
    "The name «%s» is shown in the bot; renaming inside the session failed — "
    "will retry.":
        "Имя «%s» видно в боте; в самой сессии сменить не получилось — попробую "
        "ещё раз.",
    "The name «%s» will apply in a few seconds.": "Имя «%s» применится через несколько секунд.",
    "The panel is in the topic «%s».": "Пульт — в топике «%s».",
    "→ sent to topic «%s»": "→ отправлено в топик «%s»",
    "🆕 As a new session": "🆕 Новой сессией",
    "👋 Write here — I will send it to a fitting session or start a new one. Sessions are in topics, the panel is in «%s».":
        "👋 Пишите сюда — сам отправлю в подходящую сессию или начну новую. Сессии — в топиках, пульт — в «%s».",
    "The panel is in the topic «%s». Write to the topic of the session you "
    "need.":
        "Пульт — в топике «%s». Пишите в топик нужной сессии.",
    "The question is already closed, the answer was not sent.": "Вопрос уже закрыт, ответ не отправлен.",
    "The question is already closed.": "Вопрос уже закрыт.",
    "The question did not close. Open 📺 Screen.": "Вопрос не закрылся. Откройте 📺 Экран.",
    "The question is already on another step.": "Вопрос уже на другом шаге.",
    "The session is not responding: no input line appeared within %d s. Open 📺 "
    "Screen.":
        "Сессия не отвечает: не появилась строка ввода за %d с. Откройте 📺 Экран.",
    "The session is stopped — command not sent": "Сессия остановлена — команда не отправлена",
    "The session is waiting for an answer to a question": "Сессия ждёт ответа на вопрос",
    "The session is waiting for an answer to a question — pick an option on "
    "the screen.":
        "Сессия ждёт ответа на вопрос — выберите вариант на экране.",
    "The session is waiting for an answer to a question — pick an option or ✏️ "
    "Your own answer.":
        "Сессия ждёт ответа на вопрос — выберите вариант или ✏️ Свой ответ.",
    "Thinking up names…": "Придумываю имена…",
    "This button is outdated.": "Эта кнопка устарела.",
    "This button is outdated. Open 📺 Screen or write to the session.":
        "Кнопка устарела. Откройте 📺 Экран или напишите в сессию.",
    "This button is outdated. Press «➕ New» again.": "Эта кнопка устарела. Нажмите «➕ Новая» ещё раз.",
    "This button works inside a session topic.": "Эта кнопка работает внутри топика сессии.",
    "This command works inside a session topic.": "Эта команда работает внутри топика сессии.",
    "This task was already started or cancelled.": "Эта задача уже запущена или отменена.",
    "This topic is not linked to a session. Open a session via «📋 Sessions».":
        "Этот топик не привязан к сессии. Откройте сессию через «📋 Сессии».",
    "To start a new session, write the task in this topic.":
        "Чтобы начать новую сессию, напишите задачу в этот топик.",
    "Topics removed: %d.": "Убрано топиков: %d.",
    "Topics to delete: %d. The sessions themselves stay.":
        "Будет удалено топиков: %d. Сами сессии останутся.",
    "Unknown action": "Неизвестное действие",
    "Unknown command. Help: /help": "Не знаю такой команды. Справка: /help",
    "Updated %s": "Обновлено %s",
    "Usage and limits": "Расход и лимиты",
    "Waiting for you": "Ждут вас",
    "🔐 Needs permission": "🔐 Нужно разрешение",
    "%s · group: %s · model: %s": "%s · группа: %s · модель: %s",
    "🔐 %s: needs your permission — %s": "🔐 %s: нужно ваше разрешение — %s",
    "%s: needs permission": "%s: нужно разрешение",
    "✔ Decided": "✔ решено",
    "closed": "закрыто",
    "⏳ Waiting for the owner to confirm: %s": "⏳ Жду подтверждения владельца: %s",
    "💬 Asked in the chat by %s: %s": "💬 Просят в чате, %s: %s",
    "What do you think about this?": "Что скажешь об этом?",
    "📋 The owner has not confirmed yet — it is in the backlog, nothing is lost.":
        "📋 Владелец пока не подтвердил — записал в бэклог, ничего не потеряется.",
    "▶️ Do it": "▶️ Выполнить",
    "✖ Remove": "✖ Убрать",
    "The backlog is empty.": "Бэклог пуст.",
    "Queued for the chat.": "Поставил в очередь чата.",
    "Removed from the backlog.": "Убрал из бэклога.",
    "%s asked: «%s»%s. The owner confirmed it: do it now.": "%s просил: «%s»%s. Владелец подтвердил: сделай это сейчас.",
    " (in reply to %s: «%s»)": " (в ответ на сообщение %s: «%s»)",
    "[a message without text]": "[сообщение без текста]",
    "I only see the mention. If it is a reply, that message is hidden from me (bots do not see messages sent before they joined the chat): forward it or write the question.":
        "Вижу только упоминание. Если это ответ на сообщение, мне его не видно: боты не видят сообщения, отправленные "
        "до их появления в чате. Перешлите его или напишите вопрос.",
    "The bot was asked for the id of the chat «%s»: %s": "Бота спросили об id чата «%s»: %s",
    "Could not answer now. I will pass this to the owner.": "Сейчас не получилось ответить. Передам владельцу.",
    "📨 From the chat, %s: %s\n\nAnswer: %s": "📨 Из чата, %s: %s\n\nОтвет: %s",
    "Chat responder: %s": "Ответчик в чате: %s",
    "What to do?": "Что сделать?",
    "Where to start the new session?": "Где запускать новую сессию?",
    "Working": "В работе",
    "Write a task here — I'll start a new session.": "Напишите задачу сюда — запущу новую сессию.",
    "Write here — it goes to «%s».": "Пишите сюда — уйдёт в «%s».",
    "Write now — it goes to «%s».": "Теперь пишите — уйдёт в «%s».",
    "Yes": "Да",
    "No": "Нет",
    "Allow": "Разрешить",
    "Deny": "Отклонить",
    "Always %s in this session": "Всегда %s в этой сессии",
    "Sent: %s": "Отправлено: %s",
    "The permission request is already closed.": "Запрос разрешения уже закрыт.",
    "Yes, archive": "Да, в архив",
    "Yes, close": "Да, закрыть",
    "Yes, remove": "Да, убрать",
    "Yes, stop": "Да, остановить",
    "You are signed out on all devices. Add again: /pwa":
        "Вы вышли на всех устройствах. Добавить снова: /pwa",
    "Your answer": "Ваш ответ",
    "Your answer to: ": "Ваш ответ на: ",
    "archived · %d d": "в архиве · %d дн",
    "claude stop failed": "claude stop не удался",
    "done": "готово",
    "error": "ошибка",
    "error: %s": "ошибка: %s",
    "file is unreadable": "файл не читается",
    "input": "ввода",
    "interrupted": "прервано",
    "is waiting for you: %s": "ждёт вас: %s",
    "just now": "только что",
    "marked as old": "помечена как старая",
    "needs an answer": "нужен ответ",
    "no": "нет",
    "now": "сейчас",
    "question: %s": "вопрос: %s",
    "Question: %s": "Вопрос: %s",
    "received": "получен",
    "running": "работает",
    "see details": "см. подробности",
    "session": "сессия",
    "session limit": "лимит сессии",
    "session limit · resets at %s": "лимит сессии · сброс в %s",
    "up to 28 characters": "до 28 символов",
    "waiting for you": "ждёт вас",
    "was: ": "было: ",
    "yes": "да",
    "• %s · created %s · seen %s": "• %s · создано %s · вход %s",
    "→ continued in «%s»": "→ продолжена в «%s»",
    "→ folder %s": "→ папка %s",
    "→ opened in topic «%s»": "→ открыта в топике «%s»",
    "↩ Not old": "↩ Не старая",
    "↩ Unarchive": "↩ Из архива",
    "↪ Current (№%d), previous: «%s»": "↪ Текущая (№%d), предыдущая: «%s»",
    "↪ current": "↪ текущая",
    "↪ current · %s": "↪ текущая · %s",
    "⏳ Answer sent, waiting for the session to confirm.":
        "⏳ Ответ отправлен, жду подтверждения от сессии.",
    "⏹ Stop": "⏹ Стоп",
    "⏹ Stopped.": "⏹ Остановлено.",
    "▶ Start": "▶ Запустить",
    "◀ Back": "◀ Назад",
    "◀ To active": "◀ К активным",
    "⚠️ Could not send. Open 📺 Screen.": "⚠️ Не получилось отправить. Откройте 📺 Экран.",
    "⚠️ Data is stale: it is updated while any Claude Code is open.":
        "⚠️ Данные старые: они обновляются, пока открыт любой Claude Code.",
    "⚠️ Not sent. ": "⚠️ Не отправлено. ",
    "✅ %d done": "✅ %d готово",
    "✅ %s finished": "✅ %s закончила",
    "✅ Apply all": "✅ Применить все",
    "✅ Submit answers": "✅ Отправить ответы",
    "✅ Yes": "✅ Да",
    "✏️ Name": "✏️ Имя",
    "✏️ Names": "✏️ Имена",
    "✏️ Your own answer": "✏️ Свой ответ",
    "✖ Cancel": "✖ Отмена",
    "✖ Close": "✖ Закрыть",
    "❌ No": "❌ Нет",
    "❓ Help": "❓ Справка",
    "➕ New": "➕ Новая",
    "➡ To current": "➡ К текущей",
    "🆕 started: %s (🔔 notifications on)": "🆕 запущено: %s (🔔 уведомления включены)",
    "🎛 Agents": "🎛 Агенты",
    "💬 Reply": "💬 Ответить",
    "💬 Write here": "💬 Писать сюда",
    "📁 Other folder": "📁 Другая папка",
    "📊 Quota": "📊 Квота",
    "📋 Open list": "📋 Открыть список",
    "📋 Sessions": "📋 Сессии",
    "📍 Current": "📍 Текущая",
    "📎 Sent to Telegram": "📎 Отправлено в Telegram",
    "📜 History": "📜 История",
    "📦 %d archived": "📦 %d в архиве",
    "📦 Archived": "📦 В архиве",
    "📱 App": "📱 Приложение",
    "📺 Screen": "📺 Экран",
    "🔁 Auto: off": "🔁 Авто: выкл",
    "🔁 Auto: on": "🔁 Авто: вкл",
    "🔔 Follow": "🔔 Следить",
    "🔕 Mute": "🔕 Тише",
    "🔴 %s failed: %s": "🔴 %s упала: %s",
    "🔵 %d running": "🔵 %d работают",
    "🔵 %d working": "🔵 %d в работе",
    "🔵 %s is working: %s": "🔵 %s работает: %s",
    "🗂 %d superseded": "🗂 %d сменились",
    "🗂 Archive": "🗂 В архив",
    "🗂 Archive %d": "🗂 Архив %d",
    "🗂 Archive all done": "🗂 Всё готовое в архив",
    "🗂 Archived: %s": "🗂 В архиве: %s",
    "🗂 Old": "🗂 Старая",
    "🗂 Old: continued in «%s»": "🗂 Старая: продолжена в «%s»",
    "🗑 Topic": "🗑 Топик",
    "🟡 %d waiting": "🟡 %d ждут",
    "🟡 %s is waiting: %s": "🟡 %s ждёт: %s",
    "🧹 Clean": "🧹 Очистить",
    "🧹 Remove topics": "🧹 Убрать топики",
    "Control Claude Code sessions from your phone.\n"
    "\n"
    "🎛 The panel is one message with the session list, like the claude agents "
    "screen. A number opens a session (a card with buttons). The /s command "
    "recreates the panel.\n"
    "\n"
    "Plain text goes to the current session (marked ▶). To write to another "
    "one without switching, reply (swipe) to any of its messages. Model "
    "replies arrive while a turn started by your message here is running.\n"
    "\n"
    "🔔 Notifications are off by default: press «🔔 Follow» in a session card — "
    "you will get «waiting for you», «done», «error». Sessions started from "
    "here are already followed.\n"
    "\n"
    "❓ Questions with options arrive as buttons: pick an option or «✏️ Your "
    "own answer». While a question is open, plain text is not sent to the "
    "session.\n"
    "\n"
    "Commands: /s /new /quota /help":
        "Управление сессиями Claude Code с телефона.\n"
        "\n"
        "🎛 Пульт — одно сообщение со списком сессий, как экран claude agents. "
        "Номер открывает сессию (карточка с кнопками). Команда /s пересоздаёт "
        "пульт.\n"
        "\n"
        "Обычный текст уходит в текущую сессию (отмечена ▶). Чтобы написать в "
        "другую, не переключаясь, ответьте (свайпом) на любое её сообщение. Ответы "
        "модели приходят, пока идёт ход, начатый вашим сообщением отсюда.\n"
        "\n"
        "🔔 Уведомления по умолчанию выключены: в карточке сессии нажмите «🔔 "
        "Уведомлять» — придут «ждёт вас», «готово», «ошибка». Сессии, запущенные "
        "отсюда, уже под уведомлениями.\n"
        "\n"
        "❓ Вопросы с вариантами приходят кнопками: нажмите вариант или «✏️ Свой "
        "ответ». Пока вопрос открыт, обычный текст в сессию не отправляется.\n"
        "\n"
        "Команды: /s /new /quota /help",
    "How to use\n"
    "\n"
    "• Tap a session in the panel — its topic opens.\n"
    "• Write in the session topic like in a chat, the reply comes back there.\n"
    "• Text written here goes to a fitting session or starts a new session.\n"
    "• Questions with options arrive as buttons.\n"
    "• 🔔 in the session card — notifications about it.\n"
    "• Topic icon: 🟡 waiting · ❓ question · 🔵 running · ✅ done.\n"
    "• Topics of finished sessions are removed automatically after a day.":
        "Как пользоваться\n"
        "\n"
        "• Нажмите сессию в пульте — откроется её топик.\n"
        "• В топике сессии пишите как в чате, ответ придёт туда же.\n"
        "• Текст, написанный здесь, уходит в подходящую сессию или запускает новую сессию.\n"
        "• Вопросы с вариантами приходят кнопками.\n"
        "• 🔔 в карточке сессии — уведомления о ней.\n"
        "• Значок топика: 🟡 ждёт · ❓ вопрос · 🔵 работает · ✅ готово.\n"
        "• Топики готовых сессий удаляются сами через сутки.",
    "Sign in to the «Sessions» app (%s/app/).\n"
    "\n"
    "iPhone: open the address in Safari → «Share» → «Add to Home Screen». "
    "Launch the app from the home screen and paste the code:\n"
    "\n"
    "%s\n"
    "\n"
    "Android or desktop: just press the button below.\n"
    "The code is single-use and valid for 10 minutes.":
        "Вход в приложение «Сессии» (%s/app/).\n"
        "\n"
        "iPhone: откройте адрес в Safari → «Поделиться» → «На экран Домой». "
        "Запустите приложение с экрана и вставьте код:\n"
        "\n"
        "%s\n"
        "\n"
        "Android или компьютер: можно просто нажать кнопку ниже.\n"
        "Код одноразовый, действует 10 минут.",
    "You come up with short names for background coding-agent sessions. The "
    "name is shown in a list on a phone.\n"
    "Rules:\n"
    "- in English, 2–4 words, at most 28 characters;\n"
    "- format \"Project: task\" if the project is clear (a product or repository "
    "name), otherwise just the task;\n"
    "- the name describes the TASK from the `task` field (what was asked to "
    "do), not the current state from `status`; use `status` only if `task` is "
    "empty or unclear;\n"
    "- do not use the words \"handoff\", \"continuation\", \"session\", \"prompt\", "
    "\"waiting\", \"done\";\n"
    "- no dates, ids, file names, quotes or trailing dots; keep product names "
    "as they are;\n"
    "- if the task is unclear or junk — \"No task\".\n"
    "Answer: only a JSON object {\"<id>\": \"<name>\", ...} without explanations.\n"
    "Sessions:":
        "Ты придумываешь короткие имена для фоновых сессий агента-программиста. "
        "Имя видно в списке на телефоне.\n"
        "Правила:\n"
        "- по-русски, 2–4 слова, не длиннее 28 символов;\n"
        "- формат «Проект: задача», если проект понятен (название продукта или "
        "репозитория), иначе просто задача;\n"
        "- имя описывает ЗАДАЧУ из поля task (что поручено сделать), а не текущее "
        "состояние из status; status используй, только если task пуст или "
        "непонятен;\n"
        "- не используй слова «хендофф», «продолжение», «сессия», «prompt», "
        "«ждём», «готово»;\n"
        "- без дат, id, имён файлов, кавычек и точек в конце; названия продуктов — "
        "как есть;\n"
        "- если задача непонятна или мусор — «Без задачи».\n"
        "Ответ: только JSON-объект {\"<id>\": \"<имя>\", ...} без пояснений.\n"
        "Сессии:",
    "Error: TG_ALLOWED_USERS is not set (comma-separated Telegram user ids allowed to use the bot).":
        "Ошибка: TG_ALLOWED_USERS не задан (список Telegram id через запятую, которым разрешено пользоваться ботом).",
    "⛔ Session «%s» stopped: %s":
        "⛔ Сессия «%s» остановилась: %s",
    "rate limit (rate_limit)": "лимит (rate_limit)",
    "billing (billing_error)": "оплата (billing_error)",
    "herdr is not responding.": "herdr не отвечает.",
    "herdr refused: %s": "herdr отказал: %s",
    "Not available for herdr agents": "Недоступно для агентов herdr",
    'The SDK runner is not responding.': 'Раннер SDK не отвечает.',
    'The session is busy: wait for the turn to end or interrupt it.': 'Сессия занята: дождитесь конца хода или прервите её.',
    'The session is stopped.': 'Сессия остановлена.',
    'The SDK runner has no API key.': 'У раннера SDK нет ключа API.',
    'The SDK runner refused: %s': 'Раннер SDK отказал: %s',
    'budget is used up': 'бюджет исчерпан',
    'question': 'вопрос',
    '⏸ Interrupted.': '⏸ Прервано.',
    '⟳ Continuing.': '⟳ Продолжаю.',
    'Already decided': 'Уже решено',
    'budget is used up: %s / %s $': 'бюджет исчерпан: %s / %s $',
    'permission: %s': 'разрешение: %s',
    'Not available for SDK sessions': 'Недоступно для SDK-сессий',
    '🆕 New session': '🆕 Новая сессия',
    'Processing…': 'Обрабатывается…',
    'Next text in this topic starts a session (%s).': 'Следующий текст в этом топике запустит сессию (%s).',
    '⚡ SDK': '⚡ SDK',
    '▶ subscription': '▶ подписка',
    'How to start?': 'Как запустить?',
    '▶ Subscription (Claude Code)': '▶ Подписка (Claude Code)',
    '⚡ SDK (API key)': '⚡ SDK (ключ API)',
    'Write the task in this topic.': 'Напишите задачу в этот топик.',
    '+1 $ and continue': '+1 $ и продолжить',
    '⟳ Continue': '⟳ Продолжить',
    '⏸ Interrupt': '⏸ Прервать',
    '✅ Allow': '✅ Разрешить',
    '✖ Deny': '✖ Отклонить',
    '✔ Allowed': '✔ Разрешено',
    '♾ %s allowed for the session': '♾ %s разрешён для сессии',
    '✖ Denied': '✖ Отклонено',
    'The SDK runner is not available': 'Раннер SDK недоступен',
    '♾ Always %s in this session': '♾ Всегда %s в этой сессии',
    '⚡ Via SDK (API key)': '⚡ Через SDK (ключ API)',
    ' ⚡ via SDK': ' ⚡ через SDK',
    'Control Claude sessions from your phone (SDK mode: no Claude Code on this machine).\n\n🎛 The panel is one message with the session list. A number opens a session (a card with buttons). The /s command recreates the panel.\n\nPlain text goes to the current session (marked ▶). To write to another one without switching, reply (swipe) to any of its messages. Model replies arrive while a turn started by your message here is running.\n\n🔐 When a session wants to run a tool, a message with «Allow», «Always» and «Deny» buttons arrives. ❓ Questions with options arrive as buttons too. 💲 The cost of a session is shown in its card; at the budget limit press «+1 $ and continue».\n\n🔔 Notifications are off by default: press «🔔 Follow» in a session card. Sessions started from here are already followed.\n\nCommands: /s /new /help': 'Управление сессиями Claude с телефона (режим SDK: на этой машине нет Claude Code).\n\n🎛 Панель — одно сообщение со списком сессий. Номер открывает сессию (карточка с кнопками). Команда /s пересоздаёт панель.\n\nОбычный текст уходит в текущую сессию (отмечена ▶). Чтобы написать в другую, не переключаясь, ответьте (свайпом) на любое её сообщение. Ответы модели приходят, пока идёт ход, начатый вашим сообщением отсюда.\n\n🔐 Когда сессия хочет запустить инструмент, приходит сообщение с кнопками «Разрешить», «Всегда» и «Отклонить». ❓ Вопросы с вариантами тоже приходят кнопками. 💲 Стоимость сессии видна в карточке; на лимите бюджета нажмите «+1 $ и продолжить».\n\n🔔 Уведомления по умолчанию выключены: нажмите «🔔 Следить» в карточке сессии. Сессии, запущенные отсюда, уже отслеживаются.\n\nКоманды: /s /new /help',
    'No Claude Code and the SDK runner does not respond: install one of the two (see README).': 'Нет Claude Code и не отвечает SDK-раннер: установите одно из двух (см. README).',
    'The subscription quota is not available without Claude Code.': 'Квота подписки недоступна без Claude Code.',
    ' (%d edits), first:': ' (правок: %d), первая:',
    'Where to start the new session? ⚡ via SDK': 'Где запустить новую сессию? ⚡ через SDK',
}
STR = {"ru": _RU, "en": {k: k for k in _RU}}


def set_lang(lang):
    global LANG
    LANG = lang if lang in STR else "en"
    return LANG


def t(key, **fmt):
    """Localized text for `key` (the English source text); keyword fields are applied with str.format."""
    s = STR.get(LANG, {}).get(key, key)
    return s.format(**fmt) if fmt else s


_t = t  # call-site alias: `t` is often a local variable name, `_t` never is


def variants(key):
    """The text of `key` in every language (used to recognize old reply-keyboard buttons)."""
    return {STR[lang].get(key, key) for lang in LANGS}


TMUX_PREFIX = "cctg-"
EMOJI = {"blocked": "🟡", "working": "🔵", "failed": "🔴", "done": "✅"}
RANK = {"blocked": 0, "working": 1, "failed": 2, "done": 3}
PAGE_SIZE = 8
SEEN_LIMIT = 200
TG_LIMIT = 4000
IDLE_KILL_SECONDS = 20 * 60


def log(event):
    """Event log (never message texts)."""
    print(event, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- config

def parse_env_text(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        out[k] = v
    return out


def read_env_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return parse_env_text(f.read())
    except OSError:
        return {}


def parse_user_ids(value):
    ids = set()
    for part in (value or "").split(","):
        part = part.strip().strip("\"'")
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


LEGACY_DIRS = (".config", ".local/share", ".local/state")  # legacy cc-tgbot -> zidos-pager moves


def migrate_legacy_paths(home):
    """One-time move of the legacy cc-tgbot dirs to zidos-pager (only when the new one is absent)."""
    for base in LEGACY_DIRS:
        old, new = os.path.join(home, base, "cc-tgbot"), os.path.join(home, base, "zidos-pager")  # legacy name
        if os.path.exists(new) or not os.path.isdir(old):
            continue
        try:
            os.rename(old, new)
            os.symlink(new, old)  # legacy absolute paths in old env files and units keep working
            print("zidos-pager: moved legacy %s -> %s" % (old, new), file=sys.stderr)
        except OSError as e:
            print("zidos-pager: cannot move legacy %s: %s" % (old, e), file=sys.stderr)


def legacy_env(environ, new, old):
    """An env var under its new name, falling back to the legacy name."""
    return environ.get(new) or environ.get(old)


class Config:
    def __init__(self, env_path=None, environ=None):
        environ = os.environ if environ is None else environ
        home = environ.get("HOME", os.path.expanduser("~"))
        path = (env_path or legacy_env(environ, "ZIDOS_PAGER_ENV", "CC_TGBOT_ENV")  # legacy fallback
                or os.path.join(home, ".config/zidos-pager/env"))
        self.env_path = path
        file_env = read_env_file(path)
        self.sdk_env = os.path.expanduser(file_env.get("CC_SDK_ENV") or environ.get("CC_SDK_ENV")
                                          or os.path.join(home, ".config/zidos-pager/sdk.env"))

        def get(key, default=None):
            v = file_env.get(key)
            if v is None or v == "":
                v = environ.get(key)
            return default if v is None or v == "" else v

        self.token = get("TG_BOT_TOKEN")
        # console = the owner's bot with the mini-app; responder = one group-assistant bot of `zidos_pager.py bots`
        self.role = "responder" if (get("CC_ROLE") or "").strip().lower() == "responder" else "console"
        self.api_base = get("TG_API_BASE", "https://api.telegram.org").rstrip("/")
        self.default_cwd = get("CC_DEFAULT_CWD", home)
        self.claude_bin = (get("CC_CLAUDE_BIN") or shutil.which("claude", path=environ.get("PATH", os.defpath))
                          or os.path.join(home, ".local/bin/claude"))
        # Claude Code itself: auto = used when the binary exists, off = behave as if it is not installed (SDK only)
        self.claude_mode = "off" if (get("CC_CLAUDE", "auto") or "auto").strip().lower() == "off" else "auto"
        self.lang = get("CC_LANG", "en").strip().lower()
        if self.lang not in LANGS:
            self.lang = "en"
        # Web Push needs a contact for VAPID ("mailto:you@example.com" or an https URL); without it push is off
        self.vapid_sub = (get("CC_VAPID_SUB") or "").strip()
        self.chain_generic_dirs = [os.path.expanduser(d.strip()) for d in (get("CC_CHAIN_GENERIC_DIRS") or "").split(":")
                                   if d.strip()]
        self.jobs_dir = os.path.expanduser(get("CC_JOBS_DIR", os.path.join(home, ".claude/jobs")))
        self.state_path = (get("ZIDOS_PAGER_STATE") or get("CC_TGBOT_STATE")  # legacy fallback
                           or os.path.join(home, ".local/state/zidos-pager/state.json"))
        try:
            self.webapp_port = int(get("CC_WEBAPP_PORT", "18795"))
        except ValueError:
            self.webapp_port = 18795
        self.webapp_dev = get("CC_WEBAPP_DEV") == "1"
        self.webapp_url_env = (get("CC_WEBAPP_URL") or "").strip()
        self.tunnel_file = get("CC_TUNNEL_FILE", os.path.join(home, ".local/state/zidos-pager/tunnel-url"))
        self.webapp_static = get("CC_WEBAPP_STATIC", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                  "assets", "webapp"))
        self.home = home
        self.vapid_file = get("CC_VAPID_FILE", os.path.join(home, ".config/zidos-pager/vapid.pem"))
        self.outbox_dir = get("CC_OUTBOX", os.path.join(home, ".local/state/zidos-pager/outbox"))
        self.stopfail_queue = os.path.expanduser(get("CC_STOPFAIL_QUEUE", os.path.join(
            home, ".local/state/claude-stopfailure/queue.jsonl")))
        self.rate_file = get("CC_RATE_FILE", os.path.join(home, ".claude/abtop-rate-limits.json"))
        try:
            self.topic_ttl_hours = float(get("CC_TOPIC_TTL_HOURS", "24"))
        except ValueError:
            self.topic_ttl_hours = 24.0
        self.allowed = parse_user_ids(get("TG_ALLOWED_USERS"))
        # the chat responder: one group chat whose questions go to a read-only SDK session (off without both keys)
        try:
            self.responder_chat = int(get("CC_RESPONDER_CHAT") or "")
        except ValueError:
            self.responder_chat = None
        self.responder_cwd = os.path.expanduser(get("CC_RESPONDER_CWD") or "")
        if not self.responder_cwd:
            self.responder_chat = None
        try:
            self.responder_rate = int(get("CC_RESPONDER_RATE", "30"))
        except ValueError:
            self.responder_rate = 30
        self.responder_brief = os.path.expanduser(get("CC_RESPONDER_BRIEF") or "")
        self.responder_engine = "codex" if (get("CC_RESPONDER_ENGINE") or "").strip().lower() == "codex" else "sdk"
        self.codex_model = (get("CC_CODEX_MODEL") or "").strip()
        self.responder_name = get("CC_RESPONDER_NAME") or ""  # how the chat's thread calls the bot's own answers
        self.responder_token = get("CC_RESPONDER_TOKEN")  # a separate bot for the shared chat (optional)
        self.responder_log = get("CC_RESPONDER_LOG", os.path.join(home, ".local/state/zidos-pager/responder.jsonl"))
        # herdr (second backend): auto = on when its socket exists, off = never touch it
        self.herdr_mode = "off" if (get("CC_HERDR", "auto") or "auto").strip().lower() == "off" else "auto"
        self.herdr_socket = herdr_socket_path(home, (get("CC_HERDR_SESSION") or "").strip() or None,
                                              (get("CC_HERDR_SOCKET") or "").strip() or None)
        # SDK runner (third kind of session): auto = on when its socket exists, off = never touch it
        self.sdk_mode = "off" if (get("CC_SDK", "auto") or "auto").strip().lower() == "off" else "auto"
        self.sdk_socket = os.path.expanduser((get("CC_SDK_SOCKET") or "").strip()
                                             or os.path.join(home, ".local/state/zidos-pager/sdk.sock"))
        # speech-to-text runner (voice input): auto = on when its socket exists, off = never touch it
        self.stt_mode = "off" if (get("CC_STT", "auto") or "auto").strip().lower() == "off" else "auto"
        self.stt_socket = os.path.expanduser((get("CC_STT_SOCKET") or "").strip()
                                             or os.path.join(home, ".local/state/zidos-pager/stt.sock"))
        lang_ = (get("CC_STT_LANG") or "").strip().lower()
        self.stt_lang = lang_ if re.fullmatch(r"[a-z]{2,3}", lang_) else ""  # "" = the bot language


# ------------------------------------------------------------ pure functions

def state_word(state):
    return {"blocked": _t("waiting for you"), "working": _t("running"), "done": _t("done"),
            "failed": _t("error")}.get(state, state)


BUTTON_KEYS = {"list": "📋 Sessions", "new": "➕ New", "quota": "📊 Quota", "clean": "🧹 Clean",
               "cur": "📍 Current", "screen": "📺 Screen"}  # cur/screen: legacy, no longer shown but still recognized


def btn(kind):
    return _t(BUTTON_KEYS[kind])


def button_kind(text):
    """Which (possibly legacy, in any language) reply-keyboard button is this text? -> kind or None."""
    for kind, key in BUTTON_KEYS.items():
        if text in variants(key):
            return kind
    return None


REMOVE_KB = {"remove_keyboard": True}
PANEL_MODE_TTL = 120
PANEL_CAP = 20
PANEL_DONE = 8
PANEL_PAGE = 6
PANEL_NAMES_PAGE = 8
NAME_MAX = 28
NAMES_GEN_INTERVAL = 600
NAMES_BATCH = 60
TEMP_TTL = 60
NEW_ENGINE_TTL = 600  # a chosen engine waits this long for the task text
ROUTE_RECENT = 12 * 3600  # s: sessions active this recently are routing candidates, even when done
ROUTE_STICKY = 120  # follow-up messages within this time go to the same target: a task is often split in 2-3 messages
# button presses whose handler may answer with a popup: the press is answered when the handler ends
POPUP_ACTS = frozenset(("rt", "vs", "cur", "go", "rn", "last", "stpy", "nd", "q", "pr", "sdb", "sdc", "sdi", "key", "lst", "pk"))
LOG_TTL = 48 * 3600
TOPIC_EDIT_MIN = 10
PANE_W, PANE_H = 200, 50  # the tmux window of a session
REWIDTH_EVERY = 10  # s between garbled-screen cures of one session in the mini-app
TOPIC_RENAME_SETTLE = 90  # a new name waits until it stops changing: every rename is a service message
STALE_QUOTA = 30 * 60


def panel_title():
    return _t("🎛 Agents")


def name_prompt():
    return _t('You come up with short names for background coding-agent sessions. The name is shown in a list on a phone.\nRules:\n- in English, 2–4 words, at most 28 characters;\n- format "Project: task" if the project is clear (a product or repository name), otherwise just the task;\n- the name describes the TASK from the `task` field (what was asked to do), not the current state from `status`; use `status` only if `task` is empty or unclear;\n- do not use the words "handoff", "continuation", "session", "prompt", "waiting", "done";\n- no dates, ids, file names, quotes or trailing dots; keep product names as they are;\n- if the task is unclear or junk — "No task".\nAnswer: only a JSON object {"<id>": "<name>", ...} without explanations.\nSessions:')


def route_prompt():
    return ('You route a message from a phone chat to one of the running background coding-agent sessions, or to a new one.\n'
            'Input: JSON {"message": "...", "sessions": [{"id","name","cwd","task","status"}]}.\n'
            'Rules:\n- pick a session ONLY if the message clearly continues the task of exactly that session; when in doubt, null;\n'
            '- otherwise the message starts a new session: put into "cwd" the working directory it belongs to (one of the cwd values from the list) or null if unclear.\n'
            'Answer: only a JSON object {"sid": "<id>" or null, "cwd": "<directory>" or null} without explanations.\n'
            'Input:')


def bot_commands():
    return [("s", _t("Session list")), ("new", _t("New session")), ("quota", _t("Claude quota")),
            ("screen", _t("Session screen")), ("stop", _t("Stop a session")), ("pwa", _t("App on your phone")),
            ("devices", _t("Devices of the app")), ("help", _t("Help"))]


def is_no_task(name):
    """The model's answer for an unclear task ("No task" / "Без задачи")."""
    return (name or "").lower().startswith(("no task", "без задачи"))


LONG_LIMIT = 12000
HEAD_LIMIT = 3500


def split_message(text, limit=TG_LIMIT):
    """Splits text into chunks of at most `limit` characters on line boundaries."""
    if not text or not text.strip():
        return []
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        add = line if not cur else cur + "\n" + line
        if len(add) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = add
    if cur.strip():
        chunks.append(cur)
    return chunks


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_FENCE = re.compile(r"```[^\n`]*\n?(.*?)```", re.S)
_ICODE = re.compile(r"`([^`\n]+)`")


_CTRL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]*)\)")
# a bare http(s) address in escaped text (trailing punctuation is cut off where it is linked)
_BARE_URL = re.compile(r"https?://(?:(?!&[lg]t;)[^\s<>\"\x00\x01])+")  # the text is escaped: «<», «>» are &lt; &gt;


def strip_controls(text):
    """Removes C0/C1 control characters except \\n and \\t (including NUL, ESC, \\r)."""
    return _CTRL_RE.sub("", text or "")


def _md_inline(seg, web=False):
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    seg = _ICODE.sub(keep, seg)
    seg = esc(seg)
    seg = re.sub(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$",
                 lambda m: "<b>%s</b>" % m.group(1).replace("**", ""), seg, flags=re.M)
    seg = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", seg)
    def link(m):
        url = m.group(2)
        if not re.match(r"(?i)^(https?://|mailto:)", url):
            return m.group(1)  # javascript:, data: and other schemes — text only
        extra = ' rel="noopener noreferrer" target="_blank"' if web else ""
        return '<a href="%s"%s>%s</a>' % (url.replace('"', "&quot;"), extra, m.group(1))
    links = []

    def hold(m):  # готовые <a> прячем, чтобы голые адреса не обернуть внутри них
        links.append(link(m))
        return "\x01%d\x01" % (len(links) - 1)
    seg = _LINK_RE.sub(hold, seg)
    if web:  # в мини-аппе голый адрес сам ссылкой не станет (Telegram делает это сам)
        def bare(m):  # точка, скобка, кавычка в конце — пунктуация предложения, не адрес
            url = m.group(0)
            core = url.rstrip(".,;:!?)]»'")
            if not re.match(r"https?://.", core):
                return url
            return '<a href="%s" rel="noopener noreferrer" target="_blank">%s</a>%s' % (core, core, url[len(core):])
        seg = _BARE_URL.sub(bare, seg)
    seg = re.sub(r"\x01(\d+)\x01", lambda m: links[int(m.group(1))], seg)
    return re.sub(r"\x00(\d+)\x00", lambda m: "<code>%s</code>" % esc(codes[int(m.group(1))]), seg)


# GFM table: header row, |---|:--:| separator, body rows; each line starts with |
_TABLE = re.compile(r"^[ \t]*\|.*\|[ \t]*\n[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*(?:\n|$)"
                    r"(?:[ \t]*\|.*(?:\n|$))*", re.M)


def _md_cells(line):
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _md_table(block, web):
    """A GFM table: a <table> in the mini-app (the page makes it scroll sideways), monospace <pre> in Telegram."""
    lines = block.rstrip("\n").split("\n")
    if not web:
        return "<pre>%s</pre>" % esc("\n".join(l.strip() for l in lines))
    head, body = _md_cells(lines[0]), [_md_cells(l) for l in lines[2:]]
    align = []
    for c in _md_cells(lines[1]):
        align.append(' style="text-align:center"' if c.startswith(":") and c.endswith(":")
                     else ' style="text-align:right"' if c.endswith(":") else "")
    n = len(head)

    def row(cells, tag):
        cells = (cells + [""] * n)[:n]
        return "<tr>%s</tr>" % "".join("<%s%s>%s</%s>" % (tag, align[i] if i < len(align) else "",
                                                           _md_inline(c, web), tag) for i, c in enumerate(cells))
    return '<div class="tbl"><table><thead>%s</thead><tbody>%s</tbody></table></div>' % (
        row(head, "th"), "".join(row(r, "td") for r in body))


def _md_text(seg, web):
    out, pos = [], 0
    for m in _TABLE.finditer(seg):
        out.append(_md_inline(seg[pos:m.start()], web))
        out.append(_md_table(m.group(0), web))
        pos = m.end()
    out.append(_md_inline(seg[pos:], web))
    return "".join(out)


def md_to_html(text, web=False):
    """Markdown -> HTML (Telegram parse_mode=HTML; web=True is for the mini-app: links get rel/target)."""
    text = strip_controls(text)  # internal markers \x00N\x00 must not appear in the input
    out, pos = [], 0
    for m in _FENCE.finditer(text):
        out.append(_md_text(text[pos:m.start()], web))
        out.append("<pre>%s</pre>" % esc(m.group(1).rstrip("\n")))
        pos = m.end()
    out.append(_md_text(text[pos:], web))
    return "".join(out)


def split_markdown(text, limit=3900):
    """Splits by lines BEFORE conversion; a ``` block that gets cut is closed and reopened."""
    chunks, cur, curlen, fence = [], [], 0, None
    for line in text.split("\n"):
        pieces = [line[i:i + limit] for i in range(0, len(line), limit)] or [""]
        for piece in pieces:
            add = len(piece) + 1
            reserve = 4 if fence is not None else 0
            if cur and curlen + add + reserve > limit:
                if fence is not None:
                    cur.append("```")
                chunks.append("\n".join(cur))
                cur, curlen = [], 0
                if fence is not None:
                    cur = ["```" + fence]
                    curlen = len(cur[0]) + 1
            cur.append(piece)
            curlen += add
        if line.strip().startswith("```"):
            fence = line.strip()[3:].strip() if fence is None else None
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def render_chunks(text, limit=3900):
    """[(source markdown, html)] with html length <= 4096."""
    for c in split_markdown(text, limit):
        h = md_to_html(c)
        if len(h) > 4096 and limit > 200:
            yield from render_chunks(c, limit // 2)
        else:
            yield c, h


def _tool_result_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def normalize_questions(inp):
    qs = []
    raw = inp.get("questions") if isinstance(inp, dict) else None
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        opts = [{"label": str(o.get("label", "")), "description": str(o.get("description") or "")}
                for o in (q.get("options") or []) if isinstance(o, dict)]
        if opts:
            qs.append({"question": str(q.get("question", "")), "header": str(q.get("header") or ""),
                       "multi": bool(q.get("multiSelect")), "options": opts,
                       # options with previews: the TUI puts them side by side, a digit only moves the cursor
                       "preview": any(isinstance(o, dict) and o.get("preview") for o in q.get("options") or [])})
    return qs


def parse_transcript_events(data):
    """Parses transcript bytes -> ([events], consumed_bytes).
    Events: {"kind":"text","uuid","text"}, {"kind":"ask","tool_id","questions"},
    {"kind":"answer","tool_id","content"}, {"kind":"turn_end"}.
    Only complete lines (ending with \\n) are processed."""
    end = data.rfind(b"\n")
    if end < 0:
        return [], 0
    out = []
    for raw in data[:end].split(b"\n"):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        typ = obj.get("type")
        if typ == "system":
            if obj.get("subtype") == "turn_duration":
                out.append({"kind": "turn_end"})
            continue
        if typ not in ("assistant", "user") or obj.get("isSidechain"):
            continue
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            content = [{"type": "text", "text": content}] if typ == "assistant" else []
        if not isinstance(content, list):
            continue
        if typ == "assistant":
            parts = [b.get("text", "").strip() for b in content
                     if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
            parts = [p for p in parts if p]
            if parts:
                out.append({"kind": "text", "uuid": obj.get("uuid") or "", "text": "\n\n".join(parts)})
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                    qs = normalize_questions(b.get("input"))
                    if qs and b.get("id"):
                        out.append({"kind": "ask", "tool_id": str(b["id"]), "questions": qs})
        else:
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    out.append({"kind": "answer", "tool_id": str(b["tool_use_id"]),
                                "content": _tool_result_text(b.get("content"))})
    return out, end + 1


def parse_transcript_chunk(data):
    """Model texts only: ([(uuid, text)], consumed_bytes)."""
    events, used = parse_transcript_events(data)
    return [(e["uuid"], e["text"]) for e in events if e["kind"] == "text"], used


def read_new_events(path, offset, max_bytes=4 * 1024 * 1024):
    """Reads new complete lines from a byte offset. -> (events, new_offset)."""
    try:
        size = os.path.getsize(path)
        if size < offset:
            return [], size
        if size == offset:
            return [], offset
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(max_bytes)
    except OSError:
        return [], offset
    events, used = parse_transcript_events(data)
    if used == 0 and len(data) >= max_bytes:
        used = len(data)
    return events, offset + used


def read_new_texts(path, offset, max_bytes=4 * 1024 * 1024):
    events, off = read_new_events(path, offset, max_bytes)
    return [(e["uuid"], e["text"]) for e in events if e["kind"] == "text"], off


def cut_utf16(text, units):
    """Cuts a string to a number of UTF-16 code units (how Telegram counts), never inside a surrogate pair."""
    out, n = [], 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if n + w > units:
            break
        out.append(ch)
        n += w
    return "".join(out)


def put_capped(d, key, value, limit=200):
    """Replaces the entry (it becomes the newest) and drops the oldest ones above the limit."""
    d.pop(key, None)
    d[key] = value
    while len(d) > limit:
        del d[next(iter(d))]


def answer_summary(content, answers_only=False):
    pairs = re.findall(r'"([^"]+)"="([^"]*)"', content or "")
    if pairs and answers_only:
        return short("; ".join(v for _, v in pairs), 300)
    if pairs:
        return short("; ".join("%s — %s" % kv for kv in pairs), 300)
    return short(content, 300)


def tail_texts(path, n, tail_bytes=2 * 1024 * 1024):
    """The last n model replies from the end of the file."""
    try:
        size = os.path.getsize(path)
        start = max(0, size - tail_bytes)
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    if start > 0:
        nl = data.find(b"\n")
        data = data[nl + 1:] if nl >= 0 else b""
    texts, _ = parse_transcript_chunk(data)
    return texts[-n:]


def unanswered_ask(path, tail_bytes=2 * 1024 * 1024):
    """The last AskUserQuestion without a tool_result in the log tail (or None).

    Needed when a session is opened: the question may have been asked before the bot started following it.
    """
    try:
        size = os.path.getsize(path)
        start = max(0, size - tail_bytes)
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
    except (OSError, TypeError):
        return None
    if start > 0:
        nl = data.find(b"\n")
        data = data[nl + 1:] if nl >= 0 else b""
    events, _ = parse_transcript_events(data)
    answered = {e["tool_id"] for e in events if e["kind"] == "answer"}
    for e in reversed(events):
        if e["kind"] == "ask":
            return None if e["tool_id"] in answered else e
    return None


def fallback_transcript_path(cwd, session_id, home=None):
    home = home or os.path.expanduser("~")
    if not cwd or not session_id:
        return None
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(home, ".claude", "projects", slug, session_id + ".jsonl")


def read_job_state(jobs_dir, sid):
    try:
        with open(os.path.join(jobs_dir, sid, "state.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _to_epoch(iso, fallback_ms=None):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return (fallback_ms or 0) / 1000.0


def live_state(state, st, busy=None):
    """state in state.json is rewritten with the summary and lags by minutes; tempo, inFlight and the process
    status are live: a running turn or busy process = working (unless it asked the owner), an idle one with its own timers or shells in flight
    = working, an idle one with working left behind (turn aborted with Esc) = waiting for the owner."""
    if st.get("tempo") == "active":
        return "working"
    if state == "blocked" or st.get("tempo") == "blocked":  # a question to the owner outweighs its background shells
        return "blocked"
    if st.get("tempo") is None and busy:  # no live field in state.json: the process status decides
        return "working"
    fl = st.get("inFlight")
    if isinstance(fl, dict) and (fl.get("tasks") or 0) > 0 and (busy or state == "working"):
        return "working"
    if state == "working" and busy is False:
        return "blocked"
    return state


def merge_sessions(agents, jobs_dir):
    """agents JSON + ~/.claude/jobs/<id>/state.json -> list of dicts."""
    res = []
    for a in agents if isinstance(agents, list) else []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        sid = str(a["id"])
        st = read_job_state(jobs_dir, sid)
        output = st.get("output")
        result = None
        if isinstance(output, dict):
            result = output.get("result")
        elif isinstance(output, str):
            result = output
        res.append({
            "id": sid,
            "name": a.get("name") or st.get("name") or sid,
            "cwd": a.get("cwd") or st.get("cwd") or "",
            "state": live_state(a.get("state") or st.get("state") or "working", st,
                                busy=(a.get("status") == "busy") if a.get("status") else None),
            "detail": st.get("detail") or "",
            "result": result if isinstance(result, str) and result.strip() else None,
            "sessionId": a.get("sessionId") or st.get("sessionId") or "",
            "link": st.get("linkScanPath") or "",
            "updated": _to_epoch(st.get("updatedAt"), a.get("startedAt")),
            "created": _to_epoch(st.get("createdAt"), a.get("startedAt")),
            "pid": a.get("pid"),
            "busy": (a.get("status") == "busy") if a.get("status") else None,  # the process is busy; state from state.json may be stale
            "agent_state": a.get("state") or "",  # claude agents' own state: seconds old, state.json's lags by minutes
            "name_source": st.get("nameSource") or a.get("nameSource") or "",
            "task": st.get("intent") or "",
            "needs": st.get("needs") if isinstance(st.get("needs"), str) else "",
        })
    return res


def strip_chain_label(name):
    """"🗂 Project: backend №2" -> "Project: backend" (the chain label is re-applied from current data)."""
    n = (name or "").strip()
    if n.startswith("🗂 "):
        n = n[2:].strip()
    return re.sub(r"\s+№\d+$", "", n)


def looks_like_prompt(name, task):
    """The name is really the start of the task or a technical label: long, contains a link, equals the task text,
    several lowercase English words (Claude Code auto names), or a file name."""
    n = " ".join((name or "").split())
    if not n:
        return True
    t = " ".join((task or "").split())
    if len(n) > 32 or "http" in n or "/" in n or re.search(r"\.(md|txt|json)$", n, re.I):
        return True
    if t and len(n) >= 12 and t.lower().startswith(n.lower().rstrip("…. ")):
        return True
    if re.fullmatch(r"[a-z0-9][a-z0-9 ()\-]{8,}", n):  # «design system lead setup (2)», «russian language testing»
        return True
    return False


def command_names():
    return set(SLASH_NAMES)


def clean_paste(text):
    """Text for pasting into the session terminal: no ESC or other control characters (except \\n and \\t)."""
    return strip_controls(text)


def clean_name(v):
    """A session name from the model or the owner: one line, no quotes or trailing dot, <= NAME_MAX."""
    if not isinstance(v, str):
        return ""
    v = strip_controls(v)
    v = " ".join(v.replace("«", " ").replace("»", " ").replace('"', " ").split())
    v = v.strip(" .'`")
    return v[:NAME_MAX].rstrip(" .:-")


def parse_names(out, ids):
    """Model reply (junk around the JSON is allowed) -> {id: name} for the requested ids only."""
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    res = {}
    for k, v in d.items():
        name = clean_name(v)
        if str(k) in ids and name:
            res[str(k)] = name
    return res


def parse_route(out, ids, cwds):
    """Model reply (junk around the JSON is allowed) -> (sid, cwd); each only from the offered sets, else None."""
    m = re.search(r"\{.*\}", out or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        return None, None
    sid, cwd = d.get("sid"), d.get("cwd")
    return (sid if isinstance(sid, str) and sid in ids else None,
            cwd if isinstance(cwd, str) and cwd in cwds else None)


_MD_PATH_RE = re.compile(r"(?<![\w/.])((?:~|/)[^\s`'\"«»()]*?\.md)")


HANDOFF_NAME_RE = re.compile(r"handoff|new-session|session-prompt|prompt-new|next-session|resume", re.I)


def is_handoff_name(path):
    """A handoff file: the NAME (not the path) contains handoff / new-session / session-prompt / prompt-new / next-session / resume."""
    return bool(HANDOFF_NAME_RE.search(os.path.basename(path or "")))


ARCHIVE_IDLE = 6 * 3600  # «archive done» skips sessions active within this time
CHAIN_SPINOFF_GRACE = 20 * 60  # a handoff writer active this long after the child started was not replaced


def handoff_paths(text, home=None):
    """Paths of handoff .md files in a new session's intent (absolute, normalized)."""
    home = home or os.path.expanduser("~")
    res = []
    for m in _MD_PATH_RE.finditer(text or ""):
        raw = m.group(1)
        if not is_handoff_name(raw):
            continue
        if raw.startswith("~"):
            raw = home.rstrip("/") + raw[1:]
        path = os.path.normpath(raw)
        if path not in res:
            res.append(path)
    return res


def scan_handoff_writes(path, offset, max_bytes=8 * 1024 * 1024):
    """Incrementally looks for assistant tool_use Write/Edit of handoff paths in a transcript.
    -> ({path: epoch ts of the first write}, new offset). Full json.loads only for matching lines."""
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        if size == offset:
            return {}, offset
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(max_bytes)
    except (OSError, TypeError):
        return {}, offset
    end = data.rfind(b"\n")
    if end < 0:
        return {}, offset + (len(data) if len(data) >= max_bytes else 0)
    writes = {}
    for raw in data[:end].split(b"\n"):
        if b'"tool_use"' not in raw or (b'"Write"' not in raw and b'"Edit"' not in raw):
            continue
        low = raw.lower()  # HANDOFF-….md in upper case counts too
        if not (b"handoff" in low or b"session" in low or b"prompt" in low or b"resume" in low):
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        ts = _to_epoch(obj.get("timestamp"))
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Write", "Edit"):
                fp = (b.get("input") or {}).get("file_path") if isinstance(b.get("input"), dict) else None
                if isinstance(fp, str) and is_handoff_name(fp):
                    norm = os.path.normpath(fp)
                    if norm not in writes or ts < writes[norm]:
                        writes[norm] = ts
    return writes, offset + end + 1


WEBAPP_ACTIVE = 60  # the owner touched the mini-app this recently: a permission is not pushed
PERM_FRESH = 10  # a cached tmux dialog this young is served without reading the screen
PERM_ANSWERED_TTL = 10  # an answered dialog that is still drawn is not cached/pushed again for this long
WEBAPP_QUIET = 10 * 60  # after a mini-app action nothing extra is sent to the chat/topic for the session
TG_DL_LIMIT = 20 * 1024 * 1024       # Bot API download limit
FILE_MAX = 50 * 1024 * 1024          # send limit and mini-app limit
UPLOAD_FILES_MAX = 10
UPLOAD_TOTAL_MAX = 100 * 1024 * 1024
UPLOAD_MAX = UPLOAD_TOTAL_MAX + 1024 * 1024  # the multipart body is slightly larger than the files themselves
FILE_TOKEN_TTL = 600
FILES_PER_REPLY = 6
ABS_ROOTS = ("/var/tmp", "/tmp")  # the other roots are counted from the home directory
FORBID_DIR_NAMES = {"secrets", "secret", "private", "credentials", ".git"}
FORBID_NAME_GLOBS = ("*.env", ".env*", "env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "id_*", "*_rsa*",
                     "*_ed25519*", "*_ecdsa*", "*token*", "*secret*", "*credential*", "*password*", "*passwd*",
                     "auth.json", "*.kdbx", "*history*", "*.sqlite", "*.db", "*.dump", "*.sql")
KIND_BY_EXT = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "heic"}, "pdf": {"pdf"}, "html": {"html", "htm"},
    "text": {"txt", "md", "log", "json", "csv", "tsv", "py", "js", "ts", "sh", "yml", "yaml", "toml", "xml",
             "ini", "cfg", "rs", "go", "c", "h", "cpp", "java", "sql", "diff", "patch"},
    "archive": {"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"},
    "video": {"mp4", "mov", "mkv", "webm", "avi"}, "audio": {"mp3", "wav", "ogg", "m4a", "flac", "opus"},
}
_PATH_IN_TEXT = re.compile(r"(?<![\w/.~])(~/[^\s`'\"<>()\[\]{}|]+|/[^\s`'\"<>()\[\]{}|]+)")


def _under(path, root):
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def _rel_ok(rel):
    """Inside an allowed root: no component starting with a dot, no forbidden directories or file names."""
    parts = [x for x in rel.split("/") if x]
    if not parts:
        return False
    if any(x.startswith(".") or x.lower() in FORBID_DIR_NAMES for x in parts):
        return False
    base = parts[-1].lower()
    return not any(fnmatch.fnmatch(base, g) for g in FORBID_NAME_GLOBS)


def _allowed_norm(p, homes):
    """Allow-list: ~/tg-inbox, ~/code, ~/.claude/jobs/*/tmp, /var/tmp, /tmp — everything else is forbidden."""
    for h in homes:
        h = h.rstrip("/")
        for sub in ("tg-inbox", "code"):
            if _under(p, h + "/" + sub) and _rel_ok(p[len(h + "/" + sub):]):
                return True
        m = re.match(re.escape(h) + r"/\.claude/jobs/[^/]+/tmp(/.*)$", p)
        if m and _rel_ok(m.group(1)):
            return True
    return any(_under(p, r) and _rel_ok(p[len(r):]) for r in ABS_ROOTS)


def resolve_user_path(raw, home):
    if not isinstance(raw, str) or not raw or "\0" in raw:
        return None
    if raw == "~" or raw.startswith("~/"):
        raw = home.rstrip("/") + raw[1:]
    if not raw.startswith("/"):
        return None
    return os.path.normpath(raw)


def file_ok(raw, home):
    """Is the path allowed to be sent to the owner? -> (normalized path, size) or None."""
    p = resolve_user_path(raw, home)
    if not p:
        return None
    homes = {home.rstrip("/") or "/", os.path.realpath(home)}
    rp = os.path.realpath(p)
    if not _allowed_norm(p, homes) or not _allowed_norm(rp, homes):  # a symlink to something forbidden is rejected too
        return None
    try:
        if not os.path.isfile(rp):
            return None
        size = os.path.getsize(rp)
    except OSError:
        return None
    return (p, size) if size <= FILE_MAX else None


def file_kind(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    for kind, exts in KIND_BY_EXT.items():
        if ext in exts:
            return kind
    return "other"


def find_files(text, home):
    """Existing allowed files mentioned in a reply text (up to 6, no duplicates)."""
    res, seen = [], set()
    for m in _PATH_IN_TEXT.finditer(text or ""):
        raw = m.group(1).rstrip(".,;:!?»…*")
        ok = file_ok(raw, home)
        if not ok or ok[0] in seen:
            continue
        seen.add(ok[0])
        name = os.path.basename(ok[0])
        res.append({"path": ok[0], "name": name, "size": ok[1], "kind": file_kind(name)})
        if len(res) >= FILES_PER_REPLY:
            break
    return res


def safe_filename(name):
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[^\w.\- ()]", "_", name, flags=re.U).strip(" .")
    return (name[:100] or "file")


def safe_dirname(name):
    """A folder name that keeps the session name readable («Проект: задача №2»): only / and control characters go."""
    return re.sub(r"[/\\\x00-\x1f\x7f]", "_", name or "").strip(" .")[:80]


def human_size(n):
    if n >= 1024 * 1024:
        return _t("%.1f MB") % (n / 1048576.0)
    if n >= 1024:
        return _t("%d KB") % round(n / 1024.0)
    return _t("%d B") % n


def save_inbox(home, data, name, now, folder=None):
    """~/tg-inbox[/<folder>]/YYYY-MM-DD/<name>, never overwriting (-1, -2…), files 0600, directories 0700. -> absolute path."""
    base = os.path.join(home, "tg-inbox")
    os.makedirs(base, mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)
    if folder:
        base = os.path.join(base, folder)
        os.makedirs(base, mode=0o700, exist_ok=True)
        os.chmod(base, 0o700)
    day = os.path.join(base, datetime.fromtimestamp(now).strftime("%Y-%m-%d"))
    os.makedirs(day, mode=0o700, exist_ok=True)
    os.chmod(day, 0o700)
    stem, ext = os.path.splitext(safe_filename(name))
    for i in range(1000):
        cand = os.path.join(day, (stem + ext) if i == 0 else "%s-%d%s" % (stem, i, ext))
        try:
            fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return cand
    raise OSError("could not pick a file name")


def file_message(path, size, mime, caption=""):
    t = _t("File from owner: %s (%s, %s)") % (path, human_size(size), mime or "application/octet-stream")
    return t + ("\n" + caption.strip() if caption and caption.strip() else "")


def make_file_token(path, secret, now, ttl=FILE_TOKEN_TTL):
    exp = int(now) + ttl
    sig = hmac.new(secret.encode(), ("%s|%d" % (path, exp)).encode(), hashlib.sha256).hexdigest()
    return "%s.%d.%s" % (base64.urlsafe_b64encode(path.encode()).rstrip(b"=").decode(), exp, sig)


def parse_file_token(token, secret, now):
    """-> path or None (signature, expiry)."""
    try:
        b64, exp, sig = token.split(".")
        path = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode()
        want = hmac.new(secret.encode(), ("%s|%d" % (path, int(exp))).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig) or int(exp) < now:
            return None
        return path
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def parse_multipart(content_type, raw):
    """multipart/form-data without cgi. -> ({fields}, (file name, bytes) | None) or None."""
    head = b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n"
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(head + raw)
    if not msg.is_multipart():
        return None
    fields, file = {}, None
    files = []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if name == "file":
            files.append((part.get_filename() or "file", payload))
            file = file or files[-1]
        elif name:
            fields[name] = payload.decode("utf-8", "replace")
    fields["_files"] = files  # all `file` fields in order (multiple selection in the mini-app)
    return fields, file


LOGIN_TTL = 600
LOGIN_ATTEMPTS = 5
DEVICE_TTL = 90 * 24 * 3600
DEVICE_MAX_AGE = 180 * 24 * 3600
DEVICE_TOUCH = 300
PUSH_MAX_FAILS = 5
PUSH_PAYLOAD_MAX = 3 * 1024


PUSH_HOSTS_EXACT = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")


def push_endpoint_ok(ep):
    """https on port 443 and known push services only (no IP literals or internal addresses)."""
    try:
        u = urllib.parse.urlsplit(ep)
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return False
    if u.scheme != "https" or port not in (None, 443) or u.username or u.password:
        return False
    return host in PUSH_HOSTS_EXACT or any(host.endswith(suf) and len(host) > len(suf) for suf in PUSH_HOST_SUFFIXES)


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sha256_hex(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _point(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def load_vapid_key(path):
    """VAPID P-256 key: created once and stored as PEM with mode 0600."""
    try:
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    except OSError:
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def vapid_public_key(key):
    return b64u(_point(key))


def vapid_jwt(key, aud, now, sub, ttl=12 * 3600):
    """RFC 8292: JWT ES256, signature r||s (64 bytes)."""
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": int(now) + ttl, "sub": sub}, separators=(",", ":")).encode())
    r, s_ = decode_dss_signature(key.sign(("%s.%s" % (head, claims)).encode(), ec.ECDSA(hashes.SHA256())))
    return "%s.%s.%s" % (head, claims, b64u(r.to_bytes(32, "big") + s_.to_bytes(32, "big")))


def webpush_encrypt(payload, p256dh, auth, salt=None, as_private=None):
    """RFC 8291, aes128gcm, a single record. p256dh/auth are base64url from the subscription. -> request body (bytes)."""
    ua_public = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(as_private)
    shared = as_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret,
               info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    record = payload + b"\x02"  # delimiter of the last record, no extra padding
    body = AESGCM(cek).encrypt(nonce, record, b"")
    return salt + struct.pack(">IB", 4096, len(as_public)) + as_public + body


SUB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
SUB_TAIL = 4 * 1024 * 1024
SUB_MAX = 50
SUB_FRESH = 90
SUB_ORPHAN = 3600
SUB_FAILED = {"failed", "killed", "error", "cancelled", "canceled", "stopped"}
_TS_RE = re.compile(rb'"timestamp":\s*"([^"]+)"')
_ASSISTANT_RE = re.compile(rb'"type":\s*"assistant"')


def read_tail_lines(path, limit=SUB_TAIL):
    """File tail (up to `limit` bytes) as whole lines -> bytes; the partial first line is dropped."""
    size = os.path.getsize(path)
    start = max(0, size - limit)
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    if start > 0:
        nl = data.find(b"\n")
        data = data[nl + 1:] if nl >= 0 else b""
    end = data.rfind(b"\n")
    return data[:end] if end >= 0 else data


def scan_subagent_file(path):
    """A subagent transcript: start, last entry, last text, number of tool calls (from the tail)."""
    st = os.stat(path)
    with open(path, "rb") as f:
        head = f.read(65536)
    m = _TS_RE.search(head)
    started = _to_epoch(m.group(1).decode()) if m else st.st_ctime
    last_text, tools, last_ts = "", 0, None
    for raw in read_tail_lines(path).split(b"\n"):
        if not raw.strip():
            continue
        m = _TS_RE.search(raw)
        if m:
            last_ts = _to_epoch(m.group(1).decode()) or last_ts
        if _ASSISTANT_RE.search(raw):
            tools += raw.count(b'"type":"tool_use"') + raw.count(b'"type": "tool_use"')
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            msg = obj.get("message") if isinstance(obj, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            parts = [b.get("text", "").strip() for b in (content if isinstance(content, list) else [])
                     if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
            if any(parts):
                last_text = "\n".join(x for x in parts if x)
    return {"started": started, "updated": st.st_mtime, "last_ts": last_ts or st.st_mtime,
            "last": short(last_text, 160), "tools": tools}


def scan_parent_completions(path, agent_ids, tool_ids):
    """Parent transcript tail: task-notification by agentId and tool_result by toolUseId.
    -> ({agentId: [(ts, status)]}, {toolUseId: [ts]})"""
    notes, results = {}, {}
    try:
        data = read_tail_lines(path)
    except OSError:
        return notes, results
    for raw in data.split(b"\n"):
        if b"task-notification" in raw and b"<task-id>" in raw:
            mi = re.search(rb"<task-id>([^<\\]+)</task-id>", raw)
            ms = re.search(rb"<status>([^<\\]+)</status>", raw)
            mt = _TS_RE.search(raw)
            if mi and ms and mt and mi.group(1).decode() in agent_ids:
                notes.setdefault(mi.group(1).decode(), []).append(
                    (_to_epoch(mt.group(1).decode()), ms.group(1).decode().strip().lower()))
        elif b'"tool_result"' in raw:
            mt = _TS_RE.search(raw)
            for m in re.finditer(rb'"tool_use_id":\s*"([^"]+)"', raw):
                tid = m.group(1).decode()
                if tid in tool_ids and mt:
                    results.setdefault(tid, []).append(_to_epoch(mt.group(1).decode()))
    return notes, results


def subagent_completions(notes, results, tool_use_id, background):
    """Subagent completions in ascending time order: [(ts, "done"|"failed")]. Duplicate notifications (queue + delivery) are collapsed."""
    comps = []
    for ts, status in sorted(notes):
        kind = "failed" if status in SUB_FAILED else ("done" if status == "completed" else None)
        if kind is None:
            continue
        if comps and comps[-1][1] == kind and ts - comps[-1][0] < 60:
            continue
        comps.append((ts, kind))
    if not background:  # for a background agent the first tool_result only means "started"
        comps += [(ts, "done") for ts in results.get(tool_use_id, [])]
        comps.sort()
    return comps


def subagent_status(rec, now):
    comps = rec["completions"]
    if comps and comps[-1][0] >= rec["last_ts"] - 5:
        return comps[-1][1]
    if now - rec["updated"] <= SUB_FRESH:
        return "working"
    if not comps and now - rec["updated"] > SUB_ORPHAN:
        return "done"  # quiet for a long time with no completion mark — treat as interrupted/finished
    return "working"


HISTORY_LIMIT = 24
HISTORY_TAIL = 1024 * 1024
REOPEN_AFTER = 6  # if an already open window has no input line — recreate it
TEXT_PREVIEW_EXT = (".md", ".txt", ".json", ".csv", ".log", ".py", ".js", ".ts", ".go", ".yaml", ".yml", ".toml", ".sh", ".sql")
HISTORY_TAIL_MAX = 32 * 1024 * 1024
HUGE_LINE = 200 * 1024
OUTPUT_MAX = 2000
SLASH_NAMES = ("/compact", "/model", "/effort", "/context", "/usage", "/status", "/fast", "/advisor",
               "/rename", "/clear")


def slash_commands():
    keep = _t("Also changes the default for new sessions")
    return [
        {"cmd": "/compact", "title": _t("Compact context"), "args": [], "free_arg": True,
         "hint": _t("Optional: what to keep")},
        {"cmd": "/model", "title": _t("Model"), "args": ["opus", "sonnet", "haiku", "fable"], "hint": keep},
        {"cmd": "/effort", "title": _t("Effort"), "args": ["low", "medium", "high", "xhigh", "max"], "hint": keep},
        {"cmd": "/context", "title": _t("Context")},
        {"cmd": "/usage", "title": _t("Usage and limits")},
        {"cmd": "/status", "title": _t("Status")},
        {"cmd": "/fast", "title": _t("Fast mode")},
        {"cmd": "/advisor", "title": _t("Advisor")},
        {"cmd": "/rename", "title": _t("Rename"), "free_arg": True},
        {"cmd": "/clear", "title": _t("Clear context"), "danger": True,
         "confirm": _t("Start with an empty context? The previous conversation stays on disk.")},
    ]


KEY_MAP = {**{str(i): str(i) for i in range(1, 10)}, "up": "Up", "down": "Down", "left": "Left",
           "right": "Right", "tab": "Tab", "enter": "Enter", "esc": "Escape"}
_TAG_RE = re.compile(r"<[^>]+>")
_PASTED_TAG_RE = re.compile(r"</?pasted_content\b[^>]*>")
_CMD_NAME_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.S)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_LOCAL_OUT_RE = re.compile(r"<local-command-(?:stdout|stderr)>(.*?)</local-command-(?:stdout|stderr)>", re.S)


def parse_history(data, limit=HISTORY_LIMIT, home=None, sidechain=False):
    """Transcript tail (bytes, whole lines) -> chronological list of entries for the mini-app:
    user / assistant / command / output / note. Service lines (tool_result, isMeta, task-notification…) are skipped."""
    out = []
    queued = {}  # text of a queued message -> index in out (to avoid duplicating it when it arrives as a regular entry)
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        if len(raw) > HUGE_LINE and b'"tool_result"' in raw[:4000]:
            continue  # tool result with an image/large output: not shown in the conversation
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict) or (obj.get("isSidechain") and not sidechain):
            continue
        typ = obj.get("type")
        uid, ts = obj.get("uuid") or "", obj.get("timestamp")
        # a message sent to a busy session: first the queue, then the queued_command attachment
        if typ == "queue-operation":
            qt = (obj.get("content") or "").strip() if isinstance(obj.get("content"), str) else ""
            if qt and not qt.startswith("<"):
                if obj.get("operation") == "enqueue":
                    out.append({"role": "user", "text": qt[:4000], "uuid": "q:" + (ts or ""), "ts": ts, "queued": True})
                    queued[qt] = len(out) - 1
                elif qt in queued:
                    out[queued[qt]]["queued"] = False
            continue
        att = obj.get("attachment")
        if isinstance(att, dict):
            if att.get("type") == "queued_command" and isinstance(att.get("prompt"), str):
                qt = att["prompt"].strip()
                if qt in queued:
                    out[queued[qt]]["queued"] = False
                elif qt and not qt.startswith("<"):
                    out.append({"role": "user", "text": qt[:4000], "uuid": uid, "ts": ts})
                    queued[qt] = len(out) - 1
            continue
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if typ == "assistant":
            parts = [b.get("text", "").strip() for b in (content if isinstance(content, list) else [])
                     if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
            parts = [x for x in parts if x]
            # files sent by the session via cc-send (a Bash call) are shown in the conversation too
            sent = []
            for blk in (content if isinstance(content, list) else []):
                if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "Bash":
                    cmd = str((blk.get("input") or {}).get("command") or "")
                    for m in re.finditer(r"(?:^|[;&|]\s*)cc-send\s+(\S+)", cmd):
                        sent.append(m.group(1).strip("'\""))
            hp = home or os.path.expanduser("~")
            if parts:
                full = "\n\n".join(parts)
                out.append({"role": "assistant", "html": md_to_html(full, web=True), "uuid": uid, "ts": ts,
                            "files": find_files(full + "\n" + "\n".join(sent), hp)})
            elif sent:
                files = find_files("\n".join(sent), hp)
                if files:
                    out.append({"role": "assistant", "html": _t("📎 Sent to Telegram"), "uuid": uid, "ts": ts, "files": files})
            else:
                # Claude Code drops the reply text from the log when the turn has a thinking summary: show the summary
                think = "\n\n".join(b["thinking"].strip() for b in (content if isinstance(content, list) else [])
                                     if isinstance(b, dict) and b.get("type") == "thinking"
                                     and isinstance(b.get("thinking"), str) and b["thinking"].strip())
                if think:
                    out.append({"role": "assistant", "html": md_to_html(think, web=True), "uuid": uid, "ts": ts,
                                "files": [], "thinking": True})
            continue
        if typ != "user" or obj.get("isMeta") or not isinstance(content, str):
            continue
        origin = obj.get("origin")
        kind = origin.get("kind") if isinstance(origin, dict) else None
        text = content.strip()
        if "<local-command-caveat>" in text:
            continue
        m = _CMD_NAME_RE.search(text)
        if m and kind in (None, "human"):
            name = m.group(1)
            a = _CMD_ARGS_RE.search(text)
            args = " ".join(_TAG_RE.sub("", a.group(1)).split()) if a else ""
            name = name if name.startswith("/") else "/" + name
            out.append({"role": "command", "text": (name + " " + args).strip(), "uuid": uid, "ts": ts})
            continue
        m = _LOCAL_OUT_RE.search(text)
        if m and kind in (None, "human"):
            body = strip_ansi(_TAG_RE.sub("", m.group(1))).strip()
            if body:
                out.append({"role": "output", "text": body[:OUTPUT_MAX], "uuid": uid, "ts": ts})
            continue
        if text in queued:  # already shown from the queue; an SDK run writes this turn without origin
            out[queued.pop(text)]["queued"] = False
            continue
        if kind != "human":
            continue  # task-notification and other non-human origins
        if text.startswith("[Request interrupted by user"):
            out.append({"role": "note", "text": _t("interrupted"), "uuid": uid, "ts": ts})
            continue
        if not text or "<system-reminder>" in text:
            continue
        if "pasted_content" in text:  # Claude Code wraps a multi-line paste: keep the inner text only
            text = re.sub(r"\n{3,}", "\n\n", _PASTED_TAG_RE.sub("\n", text)).strip()
            if not text:
                continue
            if text in queued:
                out[queued.pop(text)]["queued"] = False
                continue
        out.append({"role": "user", "text": text[:4000], "uuid": uid, "ts": ts})
    return out[-limit:]


def verify_init_data(init_data, token, now, max_age=24 * 3600):
    """Validates Telegram.WebApp.initData by the algorithm from the Telegram docs. -> user dict or None."""
    try:
        pairs = urllib.parse.parse_qsl(init_data or "", keep_blank_values=True)
        d = dict(pairs)
        if not pairs or len(d) != len(pairs):
            return None
        got = d.pop("hash", None)
        if not got or not token:
            return None
        check = "\n".join("%s=%s" % (k, d[k]) for k in sorted(d))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        auth_date = int(d.get("auth_date", ""))
        if now - auth_date > max_age or auth_date - now > 300:
            return None
        user = json.loads(d.get("user", ""))
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            return None
        return user
    except (ValueError, TypeError, AttributeError):
        return None


def sort_sessions(sessions):
    return sorted(sessions, key=lambda s: (RANK.get(s["state"], 4), -s["updated"]))


def parse_new_args(arg, default_cwd):
    """'/new [path] text' -> (cwd, text)."""
    arg = (arg or "").strip()
    if not arg:
        return default_cwd, ""
    first, _, rest = arg.partition(" ")
    if first[:1] in ("/", "~"):
        p = os.path.expanduser(first)
        if os.path.isdir(p):
            return p, rest.strip()
    return default_cwd, arg


def find_transitions(prev, sessions):
    """prev: {id: state}; -> (sessions to notify about, new prev)."""
    def state(s):  # a session that is still booting ("starting…") is not waiting for the owner
        if s["state"] == "blocked" and str(s.get("detail") or "").lower().startswith("starting"):
            return "working"
        return s["state"]
    out = [s for s in sessions
           if state(s) in ("blocked", "done", "failed") and prev.get(s["id"]) != state(s)]
    merged = dict(prev)
    merged.update({s["id"]: state(s) for s in sessions})
    return out, merged


def notification_text(s, short_done=False, last=None):
    name = s["name"]
    last = short_last(last)
    if s["state"] == "blocked":
        t = _t("🟡 %s is waiting: %s") % (name, humanize_detail(s["detail"]) or _t("input"))
        return t + ("\n\n" + last if last and last != s["detail"] else "")
    if s["state"] == "done":
        if short_done:
            return _t("✅ %s finished") % name
        return "✅ %s: %s" % (name, short_last(s["result"]) or last or s["detail"] or _t("done"))
    return _t("🔴 %s failed: %s") % (name, humanize_detail(s["detail"]) or _t("error"))


def short_last(t, n=1500):
    t = (t or "").strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def notify_markup(s, topics=False):
    """Buttons under a notification. In a topic the text goes to the session anyway: no «Reply» button."""
    reply = {"text": _t("💬 Reply"), "callback_data": "go:" + s["id"]}
    if is_sdk(s["id"]):  # no screen and no yes/no keys: requests have their own buttons
        rows = ([] if topics else [[reply]]) + sdk_action_rows(s)
        return {"inline_keyboard": rows} if rows else None
    rows = [([] if topics else [reply]) + [{"text": _t("📺 Screen"), "callback_data": "scr:" + s["id"]}]]
    asks = re.match(r"\s*question\b", str(s.get("detail") or ""), re.I)  # a question with options, not yes/no
    if s["state"] == "blocked" and not is_herdr(s["id"]) and not asks:  # a herdr dialog is answered with keys from 📺 Screen
        rows.insert(0, [{"text": _t("✅ Yes"), "callback_data": "qk:%s:y" % s["id"]},
                        {"text": _t("❌ No"), "callback_data": "qk:%s:n" % s["id"]}])
    return {"inline_keyboard": rows}


def tilde(path, home=None):
    home = (home or os.path.expanduser("~")).rstrip("/")
    if path == home:
        return "~"
    if home and path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


def ago(ts, now):
    if not ts or ts <= 0:
        return ""
    d = max(0, now - ts)
    if d < 60:
        return _t("just now")
    if d < 3600:
        return _t("%d min ago") % (d // 60)
    if d < 86400:
        return _t("%d h ago") % (d // 3600)
    return _t("%d d ago") % (d // 86400)


def ago_short(ts, now):
    """"3 h", "12 min", "now" — for narrow mobile Telegram lines."""
    if not ts or ts <= 0:
        return ""
    d = max(0, now - ts)
    if d < 60:
        return _t("now")
    if d < 3600:
        return _t("%d min") % (d // 60)
    if d < 86400:
        return _t("%d h") % (d // 3600)
    return _t("%d d") % (d // 86400)


def _clock24(t):
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", t.strip(), re.I)
    if not m:
        return t.strip()
    h, mi = int(m.group(1)) % 12, m.group(2) or "00"
    if m.group(3).lower() == "pm":
        h += 12
    return "%d:%s" % (h, mi)


def humanize_detail(text):
    """Technical Claude Code strings -> a short human-readable text for the list."""
    t = " ".join((text or "").split())
    m = re.search(r"hit your session limit\s*·\s*resets\s+([0-9:apm ]+)", t, re.I)
    if m:
        return _t("session limit · resets at %s") % _clock24(m.group(1))
    if re.search(r"hit your session limit", t, re.I):
        return _t("session limit")
    m = re.search(r"reached your (\w+) limit", t, re.I)
    if m:
        return _t("%s limit") % m.group(1)
    m = re.match(r"question\s*:\s*(.+)$", t, re.I)
    if m:
        return _t("Question: %s") % m.group(1)
    if t.startswith("API Error"):
        return _t("API error") + (_t(": request rejected by safeguards") if "safeguards" in t else "")
    return t


def short_keep_tag(s, n):
    """Truncates the name to n characters keeping the chain label "№K" at the end (it matters more than the name tail)."""
    m = re.match(r"^(.*?)( №\d+)$", s or "")
    if not m or len(s) <= n:
        return short(s, n)
    return short(m.group(1), max(4, n - len(m.group(2)))) + m.group(2)


def short(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def summary_line(sessions):
    c = {k: 0 for k in RANK}
    for s in sessions:
        if s["state"] in c:
            c[s["state"]] += 1
    parts = []
    if c["blocked"]:
        parts.append(_t("🟡 %d waiting") % c["blocked"])
    if c["working"]:
        parts.append(_t("🔵 %d running") % c["working"])
    if c["failed"]:
        parts.append("🔴 %d" % c["failed"])
    if c["done"]:
        parts.append("✅ %d" % c["done"])
    return " · ".join(parts)


def quota_short(data, now):
    if not data:
        return _t("Quota: no data")

    def pct(d):
        v = d.get("used_percentage") if isinstance(d, dict) else None
        return "%.0f %%" % v if isinstance(v, (int, float)) else "—"
    t = _t("Quota · 5 h %s · 7 d %s") % (pct(data.get("five_hour")), pct(data.get("seven_day")))
    upd = data.get("updated_at")
    if isinstance(upd, (int, float)) and now - upd > STALE_QUOTA:
        t += _t(" ⚠️ data is stale")
    return t


def parse_task_text(text, default_cwd):
    """Text from the panel topic -> (cwd, task). A first line that is an existing path (/… or ~…) is the folder."""
    first, _, rest = text.strip().partition("\n")
    first = first.strip()
    if first[:1] in ("/", "~"):
        if " " not in first:
            p = os.path.expanduser(first)
            if os.path.isdir(p):
                return p, rest.strip()
        else:  # "path task" on one line
            head, _, tail = first.partition(" ")
            p = os.path.expanduser(head)
            if os.path.isdir(p):
                return p, (tail.strip() + ("\n" + rest.strip() if rest.strip() else "")).strip()
    return default_cwd, text.strip()


def card_markup(sid, current=False, watched=False, topic=False, old=False, archived=False, extra=None):
    rows = []
    if not current:
        rows.append([{"text": _t("💬 Write here"), "callback_data": "cur:" + sid}])
    if is_sdk(sid):  # no screen, rename or handoff chain: history and stop work (transcript, session.stop)
        rows.append([{"text": _t("🔕 Mute") if watched else _t("🔔 Follow"), "callback_data": "watch:" + sid},
                     {"text": _t("📜 History"), "callback_data": "last:" + sid}])
        rows += extra or []
        rows.append([{"text": _t("⏹ Stop"), "callback_data": "stp:" + sid}]
                    + ([{"text": _t("🗑 Topic"), "callback_data": "tpc:" + sid}] if topic else []))
        rows.append([{"text": _t("↩ Unarchive"), "callback_data": "uar:" + sid} if archived
                     else {"text": _t("🗂 Archive"), "callback_data": "arc:" + sid}])
        if not topic:
            rows.append([{"text": btn("list"), "callback_data": "lst"}])
        return {"inline_keyboard": rows}
    if is_herdr(sid):  # no history, stop, rename or handoff chain: only follow, screen, topic, archive
        rows.append([{"text": _t("🔕 Mute") if watched else _t("🔔 Follow"), "callback_data": "watch:" + sid},
                     {"text": _t("📺 Screen"), "callback_data": "scr:" + sid}]
                    + ([{"text": _t("🗑 Topic"), "callback_data": "tpc:" + sid}] if topic else []))
        rows.append([{"text": _t("↩ Unarchive"), "callback_data": "uar:" + sid} if archived
                     else {"text": _t("🗂 Archive"), "callback_data": "arc:" + sid}])
        if not topic:
            rows.append([{"text": btn("list"), "callback_data": "lst"}])
        return {"inline_keyboard": rows}
    rows.append([{"text": _t("🔕 Mute") if watched else _t("🔔 Follow"), "callback_data": "watch:" + sid},
                 {"text": _t("📜 History"), "callback_data": "last:" + sid}])
    rows.append([{"text": _t("📺 Screen"), "callback_data": "scr:" + sid},
                 {"text": _t("⏹ Stop"), "callback_data": "stp:" + sid}]
                + ([{"text": _t("🗑 Topic"), "callback_data": "tpc:" + sid}] if topic else []))
    rows.append([{"text": _t("✏️ Name"), "callback_data": "rn:" + sid},
                 {"text": _t("↩ Not old") if old else _t("🗂 Old"), "callback_data": "cm:" + sid}])
    if old:
        rows.append([{"text": _t("➡ To current"), "callback_data": "ch:" + sid}])
    rows.append([{"text": _t("↩ Unarchive"), "callback_data": "uar:" + sid} if archived
                 else {"text": _t("🗂 Archive"), "callback_data": "arc:" + sid}])
    if not topic:
        rows.append([{"text": btn("list"), "callback_data": "lst"}])
    return {"inline_keyboard": rows}


KEYS = {"1": "1", "2": "2", "3": "3", "4": "4", "up": "Up", "dn": "Down", "ent": "Enter", "esc": "Escape"}


def screen_markup(sid):
    def b(t, k):
        return {"text": t, "callback_data": "key:%s:%s" % (sid, k)}
    return {"inline_keyboard": [
        [b("1", "1"), b("2", "2"), b("3", "3"), b("4", "4")],
        [b("↑", "up"), b("↓", "dn"), b("⏎", "ent"), b("Esc", "esc"), b("🔄", "rf")],
        [{"text": _t("✖ Close"), "callback_data": "cl"}]]}


def pane_ready(pane):
    return any(line.lstrip(" │").startswith("❯") for line in pane.splitlines())


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TOKEN_RE = re.compile(r"\x1b\[([0-9;]*)m|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[^\[]|([^\x1b]+)")
ERR_MENU = "menu"
def err_no_input_field():
    return _t("The session does not show an input field. Open 📺 Screen.")


def err_foreign_input():
    return _t("The input field contains someone else's text; not sending. Open 📺 Screen.")




def strip_ansi(s):
    return _ANSI_RE.sub("", s)


def _sgr(params, dim, rev):
    ps = params.split(";") if params else ["0"]
    i = 0
    while i < len(ps):
        p = ps[i] or "0"
        if p in ("38", "48", "58"):
            nxt = ps[i + 1] if i + 1 < len(ps) else ""
            i += 3 if nxt == "5" else 5 if nxt == "2" else 1
            continue
        if p == "0":
            dim = rev = False
        elif p == "22":
            dim = False
        elif p == "2":
            dim = True
        elif p == "27":
            rev = False
        elif p == "7":
            rev = True
        i += 1
    return dim, rev


def visible_lines(pane):
    """Snapshot lines without ANSI, dim text (ghost suggestion) and cursor (reverse video)."""
    dim = rev = False
    res = []
    for line in pane.split("\n"):
        buf = []
        for m in _TOKEN_RE.finditer(line):
            if m.group(1) is not None:
                dim, rev = _sgr(m.group(1), dim, rev)
            elif m.group(2) is not None and not dim and not rev:
                buf.append(m.group(2))
        res.append("".join(buf).replace("\xa0", " "))
    return res


def input_region(pane):
    """Compatibility: text from the last line with ❯ to the end of the screen, without dim text and ANSI."""
    lines = visible_lines(pane)
    start = None
    for i, line in enumerate(lines):
        if line.lstrip(" │").startswith("❯"):
            start = i
    return "" if start is None else "\n".join(lines[start:])


# hints that Claude Code draws in the input field; this is not typed text
INPUT_HINTS = ("Ctrl+Y to paste", "Press up to edit queued messages", "to edit queued")


def _is_frame(line):
    return line.lstrip(" │").startswith(("─", "╰", "└"))


def _input_start(lines):
    """Index of the ❯ line of the input field: the last ❯ with a frame line below it (within 15 lines)."""
    start = None
    for i, line in enumerate(lines):
        if line.lstrip(" │").startswith("❯") and any(_is_frame(l) for l in lines[i + 1:i + 16]):
            start = i
    return start


def input_field_found(pane):
    """Is the real input field (❯ above a frame line) on the screen?"""
    return _input_start(visible_lines(pane)) is not None


def input_text(pane):
    """Text in the input field (snapshot with ANSI): no ❯, no dim text, lines joined with \\n."""
    lines = visible_lines(pane)
    start = _input_start(lines)
    if start is None:
        return ""
    parts = []
    for j in range(start, len(lines)):
        line = lines[j]
        if j > start and line.lstrip(" │").startswith(("─", "╭", "╰", "└")):
            break
        if j == start:
            line = line.lstrip(" │")[1:]
        line = line.strip(" │\t").strip()
        if line and not any(h in line for h in INPUT_HINTS):
            parts.append(line)
    return "\n".join(parts)


def menu_active(pane):
    """A selection menu is shown (a question with options): "Enter to select" at the bottom."""
    lines = [l for l in strip_ansi(pane).splitlines() if l.strip()]
    text = "\n".join(lines[-8:])
    if "Enter to select" in text or ("to navigate" in text and "Esc to cancel" in text):
        return True
    # confirmation dialogs (/effort, /clear etc.): a numbered choice with the "❯ 1." cursor at the bottom of the screen, no hint
    # decided by the last line with "❯": normally an empty input field, in a dialog — the selected item
    marked = [l.replace("\xa0", " ") for l in lines if l.lstrip(" │").startswith("❯")]
    return bool(marked) and bool(re.match(r"^[\s│]*❯\s*\d+\.\s", marked[-1]))


def resume_prompt_active(pane):
    """Claude Code asks «Resume this conversation?» when a long idle session is reopened with --resume;
    the bot opened it to resume, so Enter («❯ 1. Resume») is its own choice, not the owner's question."""
    text = "\n".join([l for l in strip_ansi(pane).splitlines() if l.strip()][-8:])
    return "Resume this conversation?" in text and bool(re.search(r"❯\s*1\.\s+Resume\b", text))


_PANEL_RE = re.compile(r"Settings\s+Status\s+Config\s+Usage")


def panel_active(pane):
    """The tabbed settings panel (/usage, /status, /config, /context): no input line until Esc closes it."""
    return bool(_PANEL_RE.search(strip_ansi(pane)))


def dialog_active(pane):
    """The screen waits for keys, not for text: a selection menu or the settings panel."""
    return menu_active(pane) or panel_active(pane)


_PERM_OPT_RE = re.compile(r"^[\s│]*(❯)?\s*(\d+)\.\s+(\S.*?)\s*$")
_PERM_Q_RE = re.compile(r"^\s*Do you want\b.*\?\s*$")


def parse_perm_dialog(pane):
    """A permission dialog of Claude Code ("Do you want to proceed?" + numbered options + "Esc to cancel") ->
    {"title", "summary", "body", "note", "question", "options": [{"n", "label"}], "selected"}; None for anything
    else (a question with "Enter to select", a command menu, the input field)."""
    lines = [l.replace("\xa0", " ").rstrip() for l in strip_ansi(pane or "").splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    foot = [i for i, l in enumerate(lines) if "Esc to cancel" in l]
    if not foot:
        return None
    end = foot[-1]
    if end < len(lines) - 3 or any(k in " ".join(lines[end:]) for k in ("Enter to select", "to navigate")):
        return None
    opts, first, selected = [], None, 1
    for i in range(end - 1, -1, -1):
        m = _PERM_OPT_RE.match(lines[i])
        if m:
            opts.insert(0, {"n": int(m.group(2)), "label": m.group(3)})
            first = i
            if m.group(1):
                selected = int(m.group(2))
        elif not lines[i].strip() and not opts:
            continue
        elif opts and not lines[i].strip():
            break
        elif opts and lines[i].startswith("   "):
            continue  # a wrapped continuation of the previous label
        else:
            break
    if len(opts) < 2 or [o["n"] for o in opts] != list(range(1, len(opts) + 1)):
        return None
    qi = None
    for i in range(first - 1, max(-1, first - 4), -1):
        if _PERM_Q_RE.match(lines[i]):
            qi = i
            break
    if qi is None:
        return None
    region = lines[max(0, qi - 40):qi]
    for i in range(len(region) - 1, -1, -1):  # the dialog starts after the last solid rule
        if re.match(r"^\s*─{10,}\s*$", region[i]):
            region = region[i + 1:]
            break
    dashed = [i for i, l in enumerate(region) if re.match(r"^\s*╌{10,}\s*$", l)]
    note_lines = []
    if len(dashed) >= 2:
        head, body = region[:dashed[0]], region[dashed[0] + 1:dashed[-1]]
        tail = region[dashed[-1] + 1:]
    else:
        head = [l for l in region if l.strip()][:1]
        body = [l for l in region if l.strip()][1:]
        tail = []
        if len(dashed) == 1:
            body = [l for l in body if not re.match(r"^\s*╌{10,}\s*$", l)]
    for l in tail:
        t = l.strip().lstrip("│").strip()
        if t:
            note_lines.append(t)
    head = [l.strip() for l in head if l.strip() and not l.strip().startswith("Tip:")]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    ind = min([len(l) - len(l.lstrip()) for l in body if l.strip()] or [0])
    return {"title": head[0] if head else "", "summary": " ".join(head[1:]),
            "body": "\n".join(l[ind:].rstrip() for l in body), "note": " ".join(note_lines),
            "question": lines[qi].strip(), "options": opts, "selected": selected}


def perm_sig(perm):
    return hashlib.sha1(json.dumps([perm["title"], perm["summary"], perm["body"], perm["question"],
                                    [o["label"] for o in perm["options"]]], ensure_ascii=False).encode()).hexdigest()[:12]


def perm_label_hash(label):
    return hashlib.sha1((label or "").encode("utf-8")).hexdigest()[:4]


def perm_push_text(title, detail):
    """Push body like the Claude app: «<title>: <first line of the command>»."""
    first = next((l.strip() for l in str(detail or "").splitlines() if l.strip()), "")
    return "%s: %s" % (title, short(first, 120)) if first else str(title)


def perm_brief(title, detail):
    """One short line for the panel and the collapsed notice: «<title>: <short command>»."""
    first = next((l.strip() for l in str(detail or "").splitlines() if l.strip()), "")
    return "%s: %s" % (title, short(first, 60)) if first else str(title)


def tmux_perm_view(p):
    """A parsed tmux dialog in the shape the mini-app gets (see api_perm_view)."""
    return {"kind": "tmux", "req": None, "path": "", **p}


def sdk_perm_view(req):
    """A pending SDK permission request in the shape the mini-app gets."""
    tool = str(req.get("tool") or "?")
    head, body = sdk_input_parts(tool, req.get("input"))
    opts = [{"n": 1, "label": _t("Allow")}]
    if req.get("can_always"):
        opts.append({"n": 2, "label": _t("Always %s in this session") % short(tool, 30)})
    opts.append({"n": 3, "label": _t("Deny")})
    return {"kind": "sdk", "req": str(req.get("req") or ""), "title": tool, "summary": "", "body": body,
            "path": head, "note": "", "question": "", "options": opts, "selected": 1}


def perm_markup(sid, perm):
    """Buttons of a tmux permission dialog: one per option, numbered, «No» last; the press re-checks the label."""
    opts = sorted(perm["options"], key=lambda o: bool(re.match(r"no\b", o["label"], re.I)))
    rows = []
    for o in opts:
        lab = {"Yes": _t("Yes"), "No": _t("No")}.get(o["label"]) or o["label"].split(" · ")[0]
        pre = "%d. " % o["n"]
        rows.append([{"text": pre + short(lab, 30 - len(pre)),
                      "callback_data": "pk:%s:%d:%s" % (sid, o["n"], perm_label_hash(o["label"]))}])
    rows.append([{"text": _t("📺 Screen"), "callback_data": "scr:" + sid}])
    return {"inline_keyboard": rows}


def collapse(s):
    return " ".join((s or "").split())


def first_line_prefix(text, n=15):
    for line in text.splitlines():
        if line.strip():
            return collapse(line)[:n]
    return ""


def paste_visible(pane, text):
    """Is our text in the input field (and not a ghost suggestion or old history)?"""
    cur = collapse(input_text(pane))
    if "[Pasted text" in cur:
        return True
    pre = first_line_prefix(text)
    return bool(pre) and cur.startswith(pre)


def parse_marks(pane):
    """multiSelect marks from the screen: {number: checked} from lines like "2. [✔] label"."""
    res = {}
    for line in strip_ansi(pane).splitlines():
        m = re.match(r"^[^\w\[]*?(\d+)\.\s*\[(.)\]", line.replace("\xa0", " "))
        if m:
            res[int(m.group(1))] = m.group(2).strip() != ""
    return res


def trim_screen(pane, n=45, max_chars=3300):
    lines = [l.rstrip() for l in pane.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    lines = lines[-n:]
    while len("\n".join(lines)) > max_chars and len(lines) > 1:
        lines.pop(0)
    return "\n".join(lines)


def read_quota(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def fmt_reset(ts, now):
    t = datetime.fromtimestamp(ts)
    return t.strftime("%H:%M") if t.date() == datetime.fromtimestamp(now).date() else t.strftime("%d.%m %H:%M")


def _quota_part(label, d, five, now):
    if not isinstance(d, dict) or not isinstance(d.get("used_percentage"), (int, float)):
        return _t("%s — no data") % label
    pct = max(0.0, min(100.0, float(d["used_percentage"])))
    part = "%s — %.0f %%" % (label, pct)
    rs = d.get("resets_at") or 0
    if rs:
        part += _t(", resets at %s") % fmt_reset(rs, now)
    return part


def quota_text(data, now, full=True):
    if not data:
        return (_t("No quota data yet: the data file was not found. It is updated while any "
                   "Claude Code is open."))
    text = "Claude: %s · %s" % (_quota_part(_t("5 h"), data.get("five_hour"), True, now),
                               _quota_part(_t("7 d"), data.get("seven_day"), False, now))
    upd = data.get("updated_at")
    if full and isinstance(upd, (int, float)) and upd > 0:
        text += _t(" · data %s") % ago(upd, now)
    if isinstance(upd, (int, float)) and now - upd > STALE_QUOTA:
        text += _t("\n"
                   "⚠️ data is stale: it is updated while any Claude Code is open")
    return text


def quota_detail(data, now):
    if not data:
        return (_t("Claude quota\n"
                   "No data yet: file not found. It is updated while any Claude Code is "
                   "open."))
    lines = [_t("Claude quota"), _quota_part(_t("5 hours"), data.get("five_hour"), True, now),
             _quota_part(_t("7 days"), data.get("seven_day"), False, now)]
    upd = data.get("updated_at")
    if isinstance(upd, (int, float)) and upd > 0:
        lines.append("")
        lines.append(_t("Updated %s") % ago(upd, now))
        if now - upd > STALE_QUOTA:
            lines.append(_t("⚠️ Data is stale: it is updated while any Claude Code is open."))
    return "\n".join(lines)


def default_runner(args, timeout=30, cwd=None, input_text=None):
    """-> (returncode, stdout). Never raises."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, input=input_text)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "%s: %s" % (type(e).__name__, e)


# ------------------------------------------------------------------- tmux

# ---------------------------------------------------------------- herdr (second backend: agents of herdr panes)

HERDR_DETAIL_EVERY = 10.0      # seconds between pane.read of one pane for the list detail
HERDR_QUIET = 600.0            # a repeated connection error is logged no more often than this
HERDR_LIMIT = 4 << 20          # one reply is never longer than this
HERDR_ID_RE = re.compile(r"^h-[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
# tmux key names (KEYS / KEY_MAP) -> herdr key names
HERDR_KEYS = {"Up": "up", "Down": "down", "Left": "left", "Right": "right", "Tab": "tab", "Enter": "enter",
              "Escape": "esc", **{str(i): str(i) for i in range(1, 10)}}
HERDR_STATES = {"working": "working", "blocked": "blocked", "idle": "blocked", "done": "done",
                "unknown": "working"}  # idle/blocked = the agent waits for the owner


def herdr_sid(pane_id):
    """pane id "w1:p2" -> session id "h-w1.p2" (no ":" — it splits callback_data)."""
    return "h-" + str(pane_id).replace(":", ".")


def is_herdr(sid):
    return isinstance(sid, str) and bool(HERDR_ID_RE.match(sid))


def herdr_pane(sid):
    """session id "h-w1.p2" -> pane id "w1:p2"."""
    return sid[2:].replace(".", ":", 1)


class HerdrError(Exception):
    def __init__(self, code, message=""):
        super().__init__("%s: %s" % (code, message))
        self.code, self.message = code, message


def herdr_socket_path(home, session=None, explicit=None):
    if explicit:
        return os.path.expanduser(explicit)
    base = os.path.join(home, ".config", "herdr")
    return os.path.join(base, "sessions", session, "herdr.sock") if session else os.path.join(base, "herdr.sock")


class HerdrClient:
    """herdr socket API: newline-delimited JSON over a Unix socket, one connection per call."""

    def __init__(self, path, mode="auto"):
        self.path, self.mode = path, mode
        self._seq = 0
        self._lock = threading.Lock()

    def enabled(self):
        return self.mode != "off" and bool(self.path) and os.path.exists(self.path)

    def _connect(self, timeout):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(self.path)
        except OSError as e:
            s.close()
            raise HerdrError("unavailable", str(e))
        return s

    def _next_id(self):
        with self._lock:
            self._seq += 1
            return str(self._seq)

    @staticmethod
    def _readline(s, buf):
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise HerdrError("closed", "connection closed")
            buf += chunk
            if len(buf) > HERDR_LIMIT:
                raise HerdrError("too_big", "reply is too long")
        line, _, rest = buf.partition(b"\n")
        return line, rest

    @staticmethod
    def _parse(line):
        try:
            d = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            raise HerdrError("bad_reply", "not JSON")
        if not isinstance(d, dict):
            raise HerdrError("bad_reply", "not an object")
        return d

    def call(self, method, params=None, timeout=2.0):
        rid = self._next_id()
        s = self._connect(timeout)
        try:
            s.sendall((json.dumps({"id": rid, "method": method, "params": params or {}}) + "\n").encode())
            line, _ = self._readline(s, b"")
        except OSError as e:  # includes timeouts
            raise HerdrError("unavailable", str(e))
        finally:
            s.close()
        d = self._parse(line)
        err = d.get("error")
        if isinstance(err, dict):
            raise HerdrError(str(err.get("code") or "error"), str(err.get("message") or ""))
        res = d.get("result")
        return res if isinstance(res, dict) else {}

    def _sub_params(self, subs):
        return {"subscriptions": subs}

    def subscribe(self, subs, stop=None, on_start=None):
        """Generator of events {"event": ..., "data": {...}} on a long connection. Ends when the server closes it
        or `stop` is set; raises HerdrError when the subscription is refused or the connection breaks.
        `on_start` is called once the server has confirmed the subscription (nothing is lost after that)."""
        s = self._connect(5.0)
        try:
            s.sendall((json.dumps({"id": self._next_id(), "method": "events.subscribe",
                                   "params": self._sub_params(subs)}) + "\n").encode())
            buf = b""
            first = True
            s.settimeout(1.0)
            while not (stop is not None and stop.is_set()):
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    continue  # the partial line stays in buf
                except OSError as e:
                    raise HerdrError("unavailable", str(e))
                if not chunk:
                    raise HerdrError("closed", "connection closed")
                buf += chunk
                if len(buf) > HERDR_LIMIT and b"\n" not in buf:
                    raise HerdrError("too_big", "event is too long")
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    d = self._parse(line)
                    if first:
                        first = False
                        err = d.get("error")
                        if isinstance(err, dict):
                            raise HerdrError(str(err.get("code") or "error"), str(err.get("message") or ""))
                        if on_start is not None:
                            on_start()
                        continue  # subscription_started
                    if isinstance(d.get("event"), str):
                        yield d
        finally:
            s.close()

    # ---- thin wrappers
    def agents(self):
        return [a for a in self.call("agent.list").get("agents", []) if isinstance(a, dict) and a.get("pane_id")]

    def read(self, pane_id, source="visible", lines=None):
        p = {"pane_id": pane_id, "source": source}
        if lines:
            p["lines"] = lines
        r = self.call("pane.read", p)
        rd = r.get("read") if isinstance(r.get("read"), dict) else r
        return str(rd.get("text") or "")

    def panes(self):
        return [p for p in self.call("pane.list").get("panes", []) if isinstance(p, dict) and p.get("pane_id")]


def herdr_err_text(e):
    if e.code in ("unavailable", "closed"):
        return _t("herdr is not responding.")
    return _t("herdr refused: %s") % (e.message or e.code)


# ---------------------------------------------------------------- sdk (third kind: sessions of the SDK runner)

SDK_QUIET = 600.0              # a repeated connection error is logged no more often than this
CLAUDE_CHECK_TTL = 60.0        # how long "claude is (not) installed" is remembered
CLAUDE_GONE_TTL = 3.0          # "not installed" is usually an update replacing the binary for a second or two
STT_READY_TTL = 20.0           # how long "the STT runner is alive and has a ready engine" is remembered
STT_TIMEOUT = 600.0            # recognition may be slow (a local model on a busy machine)
VOICE_MAX = 10 * 1024 * 1024   # a voice recording from the mini-app
VOICE_TEXT_MAX = 3500          # recognised text kept for the "send" button (fits one message with the prefix)
VOICE_KEEP = 50                # recognised texts waiting for a button press
SDK_READY_TTL = 20.0           # how long "the runner is alive and has a key" is remembered
SDK_ID_RE = re.compile(r"^s-[0-9a-f]{8}$")
SDK_PERM_KEEP = 60             # open and recent permission requests kept in the store
SDK_INPUT_LIMIT = 600          # characters of a request input shown in the message
SDK_STATES = {"working": "working", "blocked": "blocked", "idle": "done", "budget": "blocked",
              "error": "failed", "interrupted": "failed", "stopped": "done"}
SDK_BUTTON_STATES = {"sdb": ("budget",), "sdc": ("interrupted", "error", "stopped"), "sdi": ("working", "blocked")}
SdkError = HerdrError          # same wire errors as herdr


def is_sdk(sid):
    return isinstance(sid, str) and bool(SDK_ID_RE.match(sid))


class SdkClient(HerdrClient):
    """The SDK runner socket: same framing as herdr, the subscription takes no parameters."""

    def _sub_params(self, subs):
        return {}

    def call(self, method, params=None, timeout=2.0):
        try:
            return super().call(method, params, timeout)
        except HerdrError as e:
            if e.code == "closed" and e.message == "connection closed":  # not the runner's "session stopped"
                raise HerdrError("unavailable", e.message)
            raise

    def sessions(self):
        return [x for x in self.call("session.list").get("sessions", [])
                if isinstance(x, dict) and is_sdk(x.get("id"))]

    def get(self, sid):
        r = self.call("session.get", {"id": sid}).get("session")
        return r if isinstance(r, dict) else {}


class SttClient(HerdrClient):
    """The speech-to-text runner socket (docs/stt-protocol.md): same framing, one request per connection."""

    def call(self, method, params=None, timeout=2.0):
        if method == "stt.transcribe":
            timeout = max(timeout, STT_TIMEOUT)
        return super().call(method, params, timeout)


def sdk_err_text(e):
    if e.code == "unavailable":
        return _t("The SDK runner is not responding.")
    if e.code == "busy":
        return _t("The session is busy: wait for the turn to end or interrupt it.")
    if e.code == "closed":
        return _t("The session is stopped.")
    if e.code == "no_key":
        return _t("The SDK runner has no API key.")
    return _t("The SDK runner refused: %s") % (e.message or e.code)


SHORT_TASK_LEN = 40


def short_task_name(text):
    """A readable name from the start of a task: the first line / sentence, cut at a word boundary with «…»."""
    t = (text or "").strip()
    first = " ".join(re.split(r"\n|(?<=[.!?])\s", t, maxsplit=1)[0].split())
    if len(first) <= SHORT_TASK_LEN:
        return first
    cut = first[:SHORT_TASK_LEN - 1]
    if first[SHORT_TASK_LEN - 1] != " " and " " in cut:  # do not cut a word in half
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"


def sdk_session_dict(x):
    """A runner session object -> the session dict of the bot (same shape as merge_sessions + kind/source)."""
    st = str(x.get("state") or "working")
    cost, budget = x.get("cost_usd"), x.get("budget_usd")
    cost = cost if isinstance(cost, (int, float)) else None
    budget = budget if isinstance(budget, (int, float)) else None
    detail = str(x.get("detail") or "")
    if st == "budget":  # own localized text: the runner's detail is English and rounds cents away
        detail = (_t("budget is used up: %s / %s $") % (usd(cost or 0), usd(budget))) if budget is not None \
            else _t("budget is used up")
    pend = [r for r in (x.get("pending") or []) if isinstance(r, dict)]
    if st == "blocked" and not detail and pend:
        detail = _t("permission: %s") % (pend[0].get("tool") or "?") if pend[0].get("kind") == "perm" else _t("question")
    num = lambda v: float(v) if isinstance(v, (int, float)) else 0.0
    raw = str(x.get("name") or "").strip()
    return {"id": x["id"], "name": short_task_name(raw) or x["id"], "cwd": str(x.get("cwd") or ""),
            "state": SDK_STATES.get(st, "working"), "detail": detail, "result": None,
            "sessionId": str(x.get("session_id") or ""), "link": "", "updated": num(x.get("updated")),
            "created": num(x.get("created")), "pid": None, "busy": None, "name_source": "sdk", "task": raw,
            "kind": "sdk", "source": "sdk", "sdk_state": st, "cost_usd": cost, "budget_usd": budget,
            "pending": pend, "always": [str(t) for t in (x.get("always") or []) if isinstance(t, str)]}


def usd(v):
    """Dollars with cents; a non-zero amount below a cent is "<0.01", not "0.00"."""
    return "<0.01" if 0 < v < 0.005 else "%.2f" % v


def sdk_cost_line(s):
    """"💲 0.12 / 1.00 $" or "" when the runner reported no cost."""
    cost, budget = s.get("cost_usd"), s.get("budget_usd")
    if not isinstance(cost, (int, float)):
        return ""
    return "💲 %s%s $" % (usd(cost), (" / " + usd(budget)) if isinstance(budget, (int, float)) else "")


def sdk_action_rows(s):
    """Extra buttons of an SDK session by its raw state: raise the budget, continue, interrupt."""
    sid, st = s["id"], s.get("sdk_state")
    if st == "budget":
        return [[{"text": _t("+1 $ and continue"), "callback_data": "sdb:" + sid}]]
    if st in SDK_BUTTON_STATES["sdc"]:
        return [[{"text": _t("⟳ Continue"), "callback_data": "sdc:" + sid}]]
    if st in ("working", "blocked"):
        return [[{"text": _t("⏸ Interrupt"), "callback_data": "sdi:" + sid}]]
    return []


def _cut(v, n):
    v = str(v)
    return v if len(v) <= n else v[:max(0, n - 1)] + "…"


def sdk_input_parts(tool, inp):
    """(head, body) of what the owner approves: head is the file path (with a note), body is the command / the start of
    the content / the change / a cut JSON. Together never longer than SDK_INPUT_LIMIT."""
    inp = inp if isinstance(inp, dict) else {}
    n = SDK_INPUT_LIMIT
    path = inp.get("file_path")
    if tool == "Bash" and inp.get("command"):
        return "", _cut(inp["command"], n)
    if path and tool == "Write" and "content" in inp:
        head = _cut(path, 200)
        return head, _cut(inp["content"], n - len(head) - 1)
    if path and tool == "Edit" and "old_string" in inp:
        head = _cut(path, 200)
        half = max(20, (n - len(head) - 6) // 2)
        return head, "%s\n→\n%s" % (_cut(inp["old_string"], half), _cut(inp.get("new_string", ""), half))
    if path and tool == "MultiEdit" and isinstance(inp.get("edits"), list) and inp["edits"]:
        head = _cut(path, 200) + _t(" (%d edits), first:") % len(inp["edits"])
        e0 = inp["edits"][0] if isinstance(inp["edits"][0], dict) else {}
        half = max(20, (n - len(head) - 6) // 2)
        return head, "%s\n→\n%s" % (_cut(e0.get("old_string", ""), half), _cut(e0.get("new_string", ""), half))
    if path and tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return _cut(path, n), ""
    return "", _cut(json.dumps(inp, ensure_ascii=False, sort_keys=True), n)


def sdk_input_text(tool, inp):
    head, body = sdk_input_parts(tool, inp)
    return head + "\n" + body if head and body else head or body


def sdk_perm_html(tool, inp):
    """Bash: the command in <pre>. Others: the path in <code>, the content / change in an expandable quote
    (a <pre> block is labelled «shell» by Telegram Desktop)."""
    head, body = sdk_input_parts(tool, inp)
    if tool == "Bash" and not head:
        return "<pre>%s</pre>" % esc(body)
    out = []
    if head:
        out.append("<code>%s</code>" % esc(head))
    if body:
        out.append("<blockquote expandable>%s</blockquote>" % esc(body))
    return "\n".join(out)


class Tmux:
    def __init__(self, claude_bin, runner=default_runner, sleep=time.sleep,
                 clock=time.monotonic, ready_timeout=40.0, verify_timeout=5.0):
        self.claude_bin = claude_bin
        self.run = runner
        self.sleep = sleep
        self.clock = clock
        self.ready_timeout = ready_timeout
        self.verify_timeout = verify_timeout
        self.last_used = {}
        self.herdr = None  # HerdrClient: ids of herdr panes (h-...) go there, not to tmux
        self.sdk = None    # SdkClient: ids of SDK sessions (s-...) go to the runner, not to tmux
        self._locks = {}
        self._glock = threading.Lock()

    def lock(self, sid):
        with self._glock:
            return self._locks.setdefault(sid, threading.Lock())

    @staticmethod
    def name(sid):
        return TMUX_PREFIX + sid

    def capture(self, sid, ansi=False):
        args = ["tmux", "capture-pane", "-p"] + (["-e"] if ansi else []) + ["-t", self.name(sid)]
        rc, out = self.run(args)
        return out if rc == 0 else ""

    def keys(self, sid, *keys):
        rc, _ = self.run(["tmux", "send-keys", "-t", self.name(sid)] + list(keys))
        self.last_used[self.name(sid)] = self.clock()
        return rc == 0

    def ensure(self, sid):
        """Makes sure a tmux window with a ready prompt (or an active menu) exists. -> (ok, error text)."""
        n = self.name(sid)
        rc, _ = self.run(["tmux", "has-session", "-t", "=" + n])
        fresh = rc != 0
        if fresh and not self._open_window(sid):
            return False, _t("Could not open the session: tmux is not responding.")
        # a window opened earlier may be broken (after a Claude Code update the screen falls apart):
        # if the input line does not show up quickly — recreate the window once
        start = self.clock()
        deadline = start + self.ready_timeout
        reopened = False
        while True:
            pane = self.capture(sid)
            if resume_prompt_active(pane) and self.clock() < deadline:
                self.run(["tmux", "send-keys", "-t", n, "Enter"])
                self.sleep(0.5)
                continue
            if pane_ready(pane) or dialog_active(pane):
                self.last_used[n] = self.clock()
                return True, ""
            if not reopened and not fresh and self.clock() - start >= REOPEN_AFTER:
                self.run(["tmux", "kill-session", "-t", "=" + n])
                if not self._open_window(sid):
                    return False, _t("Could not open the session: tmux is not responding.")
                reopened = True
                deadline = self.clock() + min(self.ready_timeout, 20)
            if self.clock() >= deadline:
                return False, (_t("The session is not responding: no input line appeared within %d s. Open "
                                  "📺 Screen.") % self.ready_timeout)
            self.sleep(0.5)

    def _open_window(self, sid):
        rc, _ = self.run(["tmux", "new-session", "-d", "-s", self.name(sid), "-x", str(PANE_W), "-y", str(PANE_H),
                          "%s attach %s" % (self.claude_bin, sid)])
        return rc == 0

    def paste_text(self, sid, text):
        """Pastes text (bracketed paste) without clearing and without Enter. Call under lock(sid). -> (ok, error)."""
        n = self.name(sid)
        text = clean_paste(text)  # ESC[201~ would end the bracketed paste early
        fd, path = tempfile.mkstemp(prefix="cctg-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(path, 0o600)
            rc, _ = self.run(["tmux", "load-buffer", "-b", n, path])
            if rc != 0:
                return False, _t("Could not pass the text to tmux.")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        rc, _ = self.run(["tmux", "paste-buffer", "-p", "-d", "-b", n, "-t", n])
        if rc != 0:
            return False, _t("Could not paste the text into the session.")
        return True, ""

    def rewidth(self, sid):
        """A one-column resize there and back, no keys: a session drawn for another client's width (one word per
        line, a phantom «text» in the field) re-renders only on SIGWINCH, Ctrl+L alone keeps the garbage."""
        for w in (PANE_W - 1, PANE_W):
            self.run(["tmux", "resize-window", "-t", self.name(sid), "-x", str(w), "-y", str(PANE_H)])
            self.sleep(0.2)

    def redraw(self, sid):
        """Ctrl+L: Claude Code redraws the screen (the conversation and the field are not changed)."""
        self.rewidth(sid)
        self.run(["tmux", "send-keys", "-t", self.name(sid), "C-l"])
        self.sleep(0.5)

    def clear_input(self, sid):
        """Empties the input field, also a multi-line one: per line C-e C-u BSpace, then C-e C-u.
        (C-e C-u removes only the current line; BSpace on an empty line joins it with the previous one.)
        The first round is sent blindly (the screen may hide our text). -> True if the field is empty."""
        n = self.name(sid)
        for _ in range(3):
            lines = max(1, len(input_text(self.capture(sid, ansi=True)).splitlines()))
            self.run(["tmux", "send-keys", "-t", n] + ["C-e", "C-u", "BSpace"] * lines + ["C-e", "C-u"])
            self.sleep(0.4)
            if not input_text(self.capture(sid, ansi=True)):
                return True
        return False

    def prepare_input(self, sid):
        """Before the paste: the input field must exist and be empty. -> error text or "" (ERR_MENU for a menu)."""
        pane = self.capture(sid, ansi=True)
        if dialog_active(pane):
            return ERR_MENU
        if not input_field_found(pane):
            self.redraw(sid)
            pane = self.capture(sid, ansi=True)
            if dialog_active(pane):
                return ERR_MENU
            if not input_field_found(pane):
                return err_no_input_field()
        if not input_text(pane):
            return ""
        if self.clear_input(sid):
            return ""
        self.redraw(sid)
        pane = self.capture(sid, ansi=True)
        if dialog_active(pane):
            return ERR_MENU
        if input_field_found(pane) and input_text(pane):
            return err_foreign_input()
        return ""

    def wait_paste(self, sid, text):
        deadline = self.clock() + self.verify_timeout
        while True:
            if paste_visible(self.capture(sid, ansi=True), text):
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(0.25)

    def send_text(self, sid, text):
        """Clears the input field, pastes the text and presses Enter only if the paste is confirmed.
        -> (ok, error); with an active question menu the error is ERR_MENU and nothing is pasted."""
        text = clean_paste(text)
        if not text or not text.strip():
            return False, _t("Not sending an empty message.")
        if is_herdr(sid):
            return self.herdr_send(sid, text)
        if is_sdk(sid):
            return self.sdk_send(sid, text)
        with self.lock(sid):
            ok, err = self.ensure(sid)
            if not ok:
                return False, err
            n = self.name(sid)
            err = self.prepare_input(sid)
            if err:
                return False, err
            for attempt in range(2):
                ok, err = self.paste_text(sid, text)
                if not ok:
                    return False, err
                seen = self.wait_paste(sid, text)
                if seen:
                    break
                if attempt == 1:
                    # the screen may be garbled: ask Claude Code to redraw it and look once more
                    self.redraw(sid)
                    if paste_visible(self.capture(sid, ansi=True), text):
                        break
                    self.clear_input(sid)  # do not leave our own text in the field
                    return False, (_t("Not sure the text reached the input field, so Enter was not pressed (to "
                                      "avoid sending the wrong thing). Open 📺 Screen."))
                # second attempt: clear the field and paste again
                self.clear_input(sid)
            self.sleep(0.5)
            rc, _ = self.run(["tmux", "send-keys", "-t", n, "Enter"])
            if rc != 0:
                return False, _t("Text pasted but Enter did not go through. Open 📺 Screen.")
            self.last_used[n] = self.clock()
            return True, ""

    def herdr_send(self, sid, text):
        """Text + Enter into a herdr pane (no field clearing: herdr agents have no verified input line)."""
        if self.herdr is None:
            return False, _t("herdr is not responding.")
        with self.lock(sid):
            try:
                self.herdr.call("pane.send_text", {"pane_id": herdr_pane(sid), "text": " ".join(text.split("\n"))})
                self.herdr.call("pane.send_keys", {"pane_id": herdr_pane(sid), "keys": ["enter"]})
            except HerdrError as e:
                return False, herdr_err_text(e)
        return True, ""

    def sdk_send(self, sid, text):
        """The text as the next message of an SDK session (busy / closed come back as readable errors)."""
        if self.sdk is None:
            return False, _t("The SDK runner is not responding.")
        try:
            self.sdk.call("session.send", {"id": sid, "text": text}, timeout=10.0)
        except SdkError as e:
            return False, sdk_err_text(e)
        return True, ""

    def send_key(self, sid, key):
        if is_sdk(sid):
            return False, _t("Not available for SDK sessions")
        if is_herdr(sid):
            hk = HERDR_KEYS.get(key)
            if self.herdr is None or hk is None:
                return False, _t("The key was not pressed.")
            try:
                self.herdr.call("pane.send_keys", {"pane_id": herdr_pane(sid), "keys": [hk]})
            except HerdrError as e:
                return False, herdr_err_text(e)
            return True, ""
        ok, err = self.ensure(sid)
        if not ok:
            return False, err
        ok = self.keys(sid, key)
        return ok, "" if ok else _t("The key was not pressed.")

    def cleanup(self):
        rc, out = self.run(["tmux", "ls", "-F", "#S"])
        if rc != 0:
            return []
        now = self.clock()
        killed = []
        for n in out.split():
            if not n.startswith(TMUX_PREFIX):
                continue
            last = self.last_used.setdefault(n, now)
            if now - last > IDLE_KILL_SECONDS:
                self.run(["tmux", "kill-session", "-t", n])
                self.last_used.pop(n, None)
                killed.append(n)
        return killed


# --------------------------------------------------------------- Telegram

class TGError(Exception):
    pass


def is_parse_error(e):
    s = str(e).lower()
    return "http 400" in s and "parse" in s


class TG:
    def __init__(self, token, base="https://api.telegram.org"):
        self.url = "%s/bot%s/" % (base.rstrip("/"), token)
        self.file_url = "%s/file/bot%s/" % (base.rstrip("/"), token)

    def _open(self, method, req, timeout):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                desc = json.loads(e.read().decode()).get("description", "")
            except Exception:
                desc = ""
            raise TGError("%s: HTTP %s %s" % (method, e.code, desc))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TGError("%s: %s" % (method, type(e).__name__))
        if not body.get("ok"):
            raise TGError("%s: %s" % (method, body.get("description", "")))
        return body.get("result")

    def call(self, method, params=None, timeout=15):
        req = urllib.request.Request(self.url + method, data=json.dumps(params or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        return self._open(method, req, timeout)

    def get_updates(self, offset):
        return self.call("getUpdates", {"offset": offset, "timeout": 30,
                                        "allowed_updates": ["message", "edited_message", "callback_query", "my_chat_member"]}, timeout=45)

    @staticmethod
    def _reply(reply_to):
        return {"message_id": reply_to, "allow_sending_without_reply": True}

    on_message = None  # hook: (chat_id, result, thread) — for the main-chat message log

    def _notify(self, chat_id, res, thread):
        if self.on_message and isinstance(res, dict):
            self.on_message(chat_id, res, thread)
        return res

    def send_one(self, chat_id, text, markup=None, reply_to=None, parse_mode=None, thread=None, silent=False):
        p = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if silent:
            p["disable_notification"] = True
        if thread:
            p["message_thread_id"] = thread
        if markup:
            p["reply_markup"] = markup
        if reply_to:
            p["reply_parameters"] = self._reply(reply_to)
        if parse_mode:
            p["parse_mode"] = parse_mode
        return self._notify(chat_id, self.call("sendMessage", p), thread)

    def send(self, chat_id, text, markup=None, reply_to=None, thread=None, silent=False):
        """Plain text, chunks <= 4000. -> list of sendMessage results."""
        chunks = split_message(text) or [_t("(empty)")]
        res = []
        for i, c in enumerate(chunks):
            res.append(self.send_one(chat_id, c, markup if i == len(chunks) - 1 else None,
                                     reply_to if i == 0 else None, thread=thread, silent=silent))
        return res

    def send_rich(self, chat_id, html, plain, markup=None, reply_to=None, thread=None, silent=False):
        """HTML; on a parse error (400) retries without parse_mode."""
        try:
            return self.send_one(chat_id, html, markup, reply_to, "HTML", thread, silent)
        except TGError as e:
            if not is_parse_error(e):
                raise
            return self.send_one(chat_id, plain, markup, reply_to, None, thread, silent)

    def send_md(self, chat_id, text, header=None, markup=None, reply_to=None, doc_name="answer.md",
                thread=None, silent=False):
        text = text.strip()
        res = []
        if len(text) > LONG_LIMIT:
            head = split_markdown(text, HEAD_LIMIT)[0] + _t("\n"
                                                            "\n"
                                                            "…full text in the attachment.")
            chunks = [(head, md_to_html(head))]
            markup_last = None
        else:
            chunks = list(render_chunks(text)) or [(_t("(empty)"), _t("(empty)"))]
            markup_last = markup
        for i, (raw, h) in enumerate(chunks):
            if i == 0 and header:
                h, raw = esc(header) + "\n\n" + h, header + "\n\n" + raw
            res.append(self.send_rich(chat_id, h, raw, markup_last if i == len(chunks) - 1 else None,
                                      reply_to if i == 0 else None, thread, silent))
        if len(text) > LONG_LIMIT:
            res.append(self.send_document(chat_id, doc_name, text.encode("utf-8"), thread=thread, silent=silent))
        return res

    def get_file(self, file_id):
        return self.call("getFile", {"file_id": file_id})

    def fetch_file(self, file_path, limit=TG_DL_LIMIT):
        """Downloads a Bot API file (up to 20 MB) fully into memory."""
        try:
            with urllib.request.urlopen(self.file_url + urllib.parse.quote(file_path), timeout=120) as r:
                data = r.read(limit + 1)
        except (urllib.error.URLError, OSError) as e:
            raise TGError("download: %s" % type(e).__name__)
        if len(data) > limit:
            raise TGError("download: file is too big")
        return data

    def send_media(self, chat_id, filename, data, caption=None, thread=None):
        """A file as in a regular chat: an image is a photo with preview, a video is a video, a GIF is an animation,
        a short text is a document with its first lines in the caption; otherwise a document.
        If Telegram rejects the photo/video (size, format), it is sent as a document."""
        ext = os.path.splitext(filename)[1].lower()
        kind = None
        if ext in (".png", ".jpg", ".jpeg", ".webp") and len(data) <= 10 * 1024 * 1024:
            kind = ("sendPhoto", "photo")
        elif ext == ".gif":
            kind = ("sendAnimation", "animation")
        elif ext in (".mp4", ".mov", ".m4v", ".webm"):
            kind = ("sendVideo", "video")
        if kind:
            try:
                return self.send_document(chat_id, filename, data, caption, thread, method=kind[0], field_name=kind[1])
            except TGError as e:
                log("send %s failed, as document: %s" % (kind[0], e))
        if ext in TEXT_PREVIEW_EXT and len(data) <= 512 * 1024:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text and text.strip():
                lines = [l.rstrip() for l in text.strip().splitlines()][:12]
                head = (caption + "\n\n") if caption else ""
                room = 1000 - len(head)
                pv = "\n".join(lines)
                pv = pv if len(pv) <= room else pv[:max(0, room - 1)] + "…"
                caption = head + pv
        return self.send_document(chat_id, filename, data, caption, thread)

    def send_document(self, chat_id, filename, data, caption=None, thread=None, method="sendDocument", field_name="document",
                      silent=False):
        boundary = "ccbot" + os.urandom(8).hex()
        parts = []

        def field(name, value):
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, name, value)).encode())

        field("chat_id", chat_id)
        if thread:
            field("message_thread_id", thread)
        if silent:
            field("disable_notification", "true")
        if caption:
            field("caption", caption)
        safe = re.sub(r"[^\w.\- ]", "_", filename)
        ctype = mimetypes.guess_type(safe)[0] or ("text/markdown" if safe.endswith(".md") else "application/octet-stream")
        if method == "sendVideo":
            field("supports_streaming", "true")
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                      "Content-Type: %s\r\n\r\n" % (boundary, field_name, safe, ctype)).encode())
        parts.append(data)
        parts.append(("\r\n--%s--\r\n" % boundary).encode())
        req = urllib.request.Request(self.url + method, data=b"".join(parts), headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary})
        return self._notify(chat_id, self._open(method, req, 120), thread)

    def edit(self, chat_id, message_id, text, markup=None, html=None):
        """Edits text. If html is given it goes first, on a 400 parse error plain text is used."""
        def go(t, pm):
            p = {"chat_id": chat_id, "message_id": message_id, "text": t or _t("(empty)")}
            if markup:
                p["reply_markup"] = markup
            if pm:
                p["parse_mode"] = pm
            return self.call("editMessageText", p)
        try:
            try:
                return go(html, "HTML") if html is not None else go(text[:TG_LIMIT], None)
            except TGError as e:
                if html is not None and is_parse_error(e):
                    return go(text[:TG_LIMIT], None)
                raise
        except TGError as e:
            if "not modified" not in str(e):
                raise

    def edit_markup(self, chat_id, message_id, markup=None):
        try:
            return self.call("editMessageReplyMarkup", {
                "chat_id": chat_id, "message_id": message_id,
                "reply_markup": markup or {"inline_keyboard": []}})
        except TGError as e:
            if "not modified" not in str(e):
                raise

    def delete(self, chat_id, message_id):
        try:
            self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except TGError:
            pass

    def react(self, chat_id, message_id, emoji="👀"):
        self.call("setMessageReaction", {"chat_id": chat_id, "message_id": message_id,
                                         "reaction": [{"type": "emoji", "emoji": emoji}] if emoji else []})

    def typing(self, chat_id, thread=None):
        p = {"chat_id": chat_id, "action": "typing"}
        if thread:
            p["message_thread_id"] = thread
        try:
            self.call("sendChatAction", p)
        except TGError:
            pass

    def get_me(self):
        return self.call("getMe")

    def create_topic(self, chat_id, name):
        return self.call("createForumTopic", {"chat_id": chat_id, "name": name[:128]})["message_thread_id"]

    def edit_topic(self, chat_id, thread, name):
        self.call("editForumTopic", {"chat_id": chat_id, "message_thread_id": thread, "name": name[:128]})

    def delete_topic(self, chat_id, thread):
        self.call("deleteForumTopic", {"chat_id": chat_id, "message_thread_id": thread})

    def pin(self, chat_id, message_id):
        self.call("pinChatMessage", {"chat_id": chat_id, "message_id": message_id, "disable_notification": True})

    def delete_messages(self, chat_id, ids):
        try:
            self.call("deleteMessages", {"chat_id": chat_id, "message_ids": list(ids)})
        except TGError:
            pass

    def answer(self, cb_id, text=None, alert=False):
        p = {"callback_query_id": cb_id}
        if text:
            p["text"] = cut_utf16(text, 200)  # Telegram's limit for a popup
            if alert:
                p["show_alert"] = True
        try:
            self.call("answerCallbackQuery", p)
        except TGError:
            return False
        return True

    def set_commands(self):
        self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in bot_commands()]})


# ---------------------------------------------------------------- state

class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"chats": {}, "states": {}, "offset": 0, "msgmap": {}, "watched": [], "questions": {},
                     "qmap": {}, "qseq": 0, "msglog": [], "singles": {}, "tracker": {}, "panel": {},
                     "names": {}, "auto_names": False, "names_last_gen": 0,
                     "chain_scan": {}, "chain_links": {}, "chain_manual": {}, "archived": {},
                     "devices": {}, "logins": {}, "push": {}}
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.pop("muted", None)  # old scheme (mutes): notifications are now off by default
                self.data.update(d)
        except (OSError, ValueError):
            pass

    def save(self):
        with self.lock:
            try:
                # the lock only orders writers: another thread may change the data while it is serialized
                for attempt in range(5):
                    try:
                        text = json.dumps(self.data, ensure_ascii=False)
                        break
                    except RuntimeError:  # "dictionary changed size during iteration"
                        if attempt == 4:
                            raise
                        time.sleep(0.01)
                os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
                tmp = self.path + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, self.path)
            except (OSError, TypeError, ValueError, RuntimeError) as e:
                log("state save failed: %s" % type(e).__name__)

    def chat(self, chat_id):
        c = self.data["chats"].setdefault(str(chat_id), {})
        c.setdefault("session", None)
        c.setdefault("fw", {})
        c.setdefault("pending_new", None)
        c.setdefault("new_dirs", [])
        c.setdefault("pending_own", None)
        c.setdefault("topics", {})
        c.setdefault("pending_task", None)
        c.setdefault("pending_rename", None)
        c.setdefault("cards", {})
        c.setdefault("notes", {})
        c.setdefault("new_engine", None)
        return c


# --------------------------------------------------------------------- bot

PERM_OPEN_RETRY = 60  # s between attempts to open a window for a blocked session
STOPFAIL_REPEAT = 1800  # s: a session retrying against a limit writes a record per attempt; one notice is enough
RESPONDER_TURNS = 10  # questions per responder session: what chat participants wrote does not pile up for long
# a chat message that asks for a change is answered by the stronger model (questions stay on the default one)
RESPONDER_CHANGE_MODEL = "opus"
_CHANGE_RE = re.compile(r"\b(сдела|добав|измен|исправ|поправ|почин|созда|удал|убер|убра|замен|поменя|перепиш|обнов|переимен"
                        r"|закоммит|запуш|внес|допиш|реализ|add|fix|change|create|delete|remove|update|rename"
                        r"|commit|implement)", re.I)


def asks_change(text):
    """Words of a change request (shortcut: word stems, not intent; a question with «изменения» goes to opus too)."""
    return bool(_CHANGE_RE.search(text or ""))
CHAT_TAIL = 12  # last chat messages put into every question
RESPONDER_WAIT = 1800  # s: a change the owner has not decided by then goes to the backlog, and the chat moves on
RESPONDER_RUNNER_WAIT = 60  # s: a question waits this long for a runner that is still starting
RESPONDER_TIMEOUT = 600  # s: a question without a result by then is given up, so the queue never stalls


def quota_stop(ev):
    """The stop is a shared quota or an empty balance: a fresh session would hit it too."""
    return bool(re.search(r"rate_limit|session limit|usage limit|hit your limit|credit balance", json.dumps(ev, default=str), re.I))


def codex_boxed(cwd, prompt, model=""):
    """Codex for the chat: its «read-only» sandbox still reads the whole home (bot tokens), so it runs in bwrap that
    sees only the system, Codex itself and the chat clone. No bwrap -> OSError: the fallback fails closed."""
    import shutil
    bw, codex = shutil.which("bwrap"), shutil.which(os.environ.get("CC_CODEX_BIN") or "codex")
    if not bw or not codex:
        raise OSError("bwrap or codex missing")
    home, real = os.path.expanduser("~"), os.path.realpath(codex)
    pkg = os.path.dirname(os.path.dirname(real))
    # an empty ~/.codex with only its login: the owner's hooks and config stay out (a hook blocked every prompt here)
    auth = os.path.join(home, ".codex", "auth.json")
    if not os.path.isfile(auth):
        raise OSError("codex is not signed in")
    return [bw, "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", home,
            "--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve",  # /etc/resolv.conf points there: no DNS without it
            "--tmpfs", os.path.join(home, ".codex"), "--bind", auth, auth,
            "--clearenv", "--setenv", "HOME", home, "--setenv", "PATH", "/usr/bin:/bin",
            "--ro-bind", pkg, pkg, "--ro-bind", os.path.dirname(codex), os.path.dirname(codex), "--ro-bind", cwd, cwd,
            "--unshare-pid", "--die-with-parent", "--chdir", cwd,
            real, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-C", cwd] + (["-m", model] if model else []) + ["--", prompt]


# the mini-app's «Model» sheet: provider -> Anthropic-compatible address, default model, key variable
MODEL_PRESETS = {
    "deepseek": {"title": "DeepSeek", "base": "https://api.deepseek.com/anthropic", "model": "deepseek-flash", "var": "DEEPSEEK_API_KEY"},
    "glm": {"title": "GLM", "base": "https://api.z.ai/api/anthropic", "model": "glm-5.3", "var": "GLM_API_KEY"},
    "kimi": {"title": "Kimi", "base": "https://api.kimi.com/coding", "model": "kimi-for-coding", "var": "KIMI_API_KEY"},
    "openrouter": {"title": "OpenRouter", "base": "https://openrouter.ai/api", "model": "deepseek/deepseek-v4.1-flash", "var": "OPENROUTER_API_KEY"},
    "anthropic": {"title": "Claude API", "base": "", "model": "sonnet", "var": "ANTHROPIC_API_KEY"},
    "custom": {"title": "", "base": "", "model": "", "var": "CUSTOM_API_KEY"},
    "codex": {"title": "Codex", "base": None, "model": "", "var": None},
}
# the console's own model only (not group bots): the installed Claude Code CLI with its own sign-in, no key
CLAUDE_LOGIN = {"id": "claude_login", "title": "Claude Code", "model": "sonnet"}
MODEL_CHOICES = {"anthropic": ["opus", "sonnet", "haiku"], "claude_login": ["opus", "sonnet", "haiku"]}
_MODEL_NAME = re.compile(r"[\w./:\[\]-]{1,120}")
_MODEL_KEY = re.compile(r"[\x21-\x7e]{8,512}")


def env_read(path):
    """NAME=value lines of an env file -> dict (surrounding quotes dropped)."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, eq, value = line.strip().partition("=")
                if eq and name and not name.startswith("#"):
                    out[name] = value.strip().strip('"\'')
    except OSError:
        pass
    return out


def env_update(path, updates):
    """Sets NAME=value lines (None removes one) and keeps everything else: written whole, mode 0600."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    left = dict(updates)
    out = []
    for line in lines:
        name = line.partition("=")[0].strip()
        if "=" in line and name in updates:
            value = left.pop(name, None)
            if value is not None:
                out.append("%s=%s" % (name, value))
        else:
            out.append(line)
    out += ["%s=%s" % (k, v) for k, v in left.items() if v is not None]
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)  # mkstemp made it 0600


def bots_alias(base, model):
    """The Messages API does not know the CLI aliases (the runner does): sonnet -> a full model id on api.anthropic.com."""
    return {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}.get(model, model) if not base else model


def model_check(base, key, model):
    """One tiny request to the provider. -> "ok", "key", "balance", "limit", "model" or "net"."""
    model = bots_alias(base, model)
    url = (base or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    body = json.dumps({"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "ok"}]}).encode()
    req = urllib.request.Request(url, body, {"x-api-key": key, "anthropic-version": "2023-06-01",
                                             "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25):
            return "ok"
    except urllib.error.HTTPError as e:
        return {401: "key", 403: "key", 402: "balance", 429: "limit", 400: "model", 404: "model"}.get(e.code, "net")
    except (OSError, ValueError):
        return "net"


# ---------------------------------------------------------------- group-assistant bots (`zidos_pager.py bots`)
# One bot = its own registry dir (env, sdk.env, keys.env, about.md, brief.md, persona.json) and its own process pair:
# zidos_pager.py in the responder role + sdk_runner.py. See README, section "Several bots".

BOT_SLUG = re.compile(r"[a-z0-9_]{1,40}")
BOT_HTTPS = re.compile(r"https://[^\s\"'\\]+")
PERSONA_LIMITS = (("name", 64), ("short", 120), ("description", 512))
PERSONA_SYSTEM = (
    "You set up a Telegram assistant bot for a group chat from the owner's description of a project. "
    "Answer with ONE JSON object and nothing else (no markdown fences), with the string fields: "
    "\"name\" (the bot's display name, at most 64 characters), \"short\" (one-line about text, at most 120 characters), "
    "\"description\" (what the bot is, shown before the first message, at most 512 characters), "
    "\"brief\" (the bot's instructions in markdown: its role, tone, what it knows about the project, rules, "
    "what it does not do, and how it answers when it does not know). "
    "Write every field in the language of the owner's description.")


def bots_dirs(cfg, slug):
    return (os.path.join(cfg.home, ".config/zidos-pager/bots", slug), os.path.join(cfg.home, ".local/state/zidos-pager/bots", slug))


def bots_clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


def bots_write(path, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)  # mkstemp made it 0600


def bots_read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def bots_service(action, slug):
    """The only place that starts or stops a bot's process pair. action: start | restart | stop | active."""
    names = ("zidos-pager-bot@" + slug, "zidos-pager-bot-sdk@" + slug)
    if platform.system() == "Darwin":
        return bots_launchd(action, slug)
    cmd = {"start": ["enable", "--now"], "restart": ["restart"], "stop": ["disable", "--now"],
           "active": ["is-active", "--quiet"]}[action]
    try:
        return subprocess.run(["systemctl", "--user"] + cmd + list(names[:1] if action == "active" else names),
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def bots_plist(kind, slug, home, repo, config_dir):
    path_env = home + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    if kind == "bot":
        program = [sys.executable, "-u", repo + "/zidos_pager.py"]
        env = {"HOME": home, "PATH": path_env, "ZIDOS_PAGER_ENV": config_dir + "/env"}
    else:
        program = [repo + "/.venv-sdk/bin/python", "-u", repo + "/sdk_runner.py", "serve"]
        env = {"HOME": home, "PATH": path_env}
        env.update({k: v for k, v in read_env_file(config_dir + "/sdk.env").items() if k.startswith("CC_SDK_") or k == "CC_CHAT_SITE"})
    log_file = "%s/Library/Logs/zidos-pager/%s-%s.log" % (home, kind, slug)
    return {"Label": "dev.zidos.pager.%s.%s" % (kind, slug), "ProgramArguments": program, "EnvironmentVariables": env,
            "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Background",
            "StandardOutPath": log_file, "StandardErrorPath": log_file}


def bots_launchd(action, slug):
    import plistlib
    home = os.path.expanduser("~")
    repo = os.path.dirname(os.path.abspath(__file__))
    config_dir = os.path.join(home, ".config/zidos-pager/bots", slug)
    gui = "gui/%d" % os.getuid()

    def launchctl(*args):
        try:
            return subprocess.run(["launchctl"] + list(args), capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    ok = True
    for kind in ("bot", "sdk"):
        label = "dev.zidos.pager.%s.%s" % (kind, slug)
        target = os.path.join(home, "Library/LaunchAgents", label + ".plist")
        if action == "active":
            return launchctl("print", "%s/%s" % (gui, label)) if kind == "bot" else ok
        if action in ("start", "restart"):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.makedirs(os.path.join(home, "Library/Logs/zidos-pager"), exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                plistlib.dump(bots_plist(kind, slug, home, repo, config_dir), f)
            launchctl("bootout", "%s/%s" % (gui, label))
            for _ in range(50):  # bootout returns before the job is gone; an early bootstrap fails with I/O error
                if not launchctl("print", "%s/%s" % (gui, label)):
                    break
                time.sleep(0.2)
            ok = launchctl("bootstrap", gui, target) and ok
        else:
            launchctl("bootout", "%s/%s" % (gui, label))
            if action == "stop":
                try:
                    os.remove(target)
                except OSError:
                    pass
    return ok


def bots_provider(sdk, engine):
    if engine == "codex":
        return "codex"
    if not sdk.get("CC_SDK_MODEL"):
        return ""
    base = sdk.get("CC_SDK_BASE_URL", "")
    return next((k for k, p in MODEL_PRESETS.items() if p["base"] == base and p["base"] is not None and k != "custom"
                 and (base or k == "anthropic")), "custom")


def bots_get(cfg, slug):
    cdir, sdir = bots_dirs(cfg, slug)
    env, sdk = read_env_file(cdir + "/env"), read_env_file(cdir + "/sdk.env")
    try:
        groups = json.loads(bots_read(sdir + "/state.json") or "{}").get("groups") or {}
    except (ValueError, AttributeError):
        groups = {}
    try:
        chat = int(env.get("CC_RESPONDER_CHAT") or "")
    except ValueError:
        chat = None
    try:
        persona = json.loads(bots_read(cdir + "/persona.json") or "{}")
    except ValueError:
        persona = {}
    keys = env_read(cdir + "/keys.env")
    cwd = env.get("CC_RESPONDER_CWD", "")
    engine = "codex" if env.get("CC_RESPONDER_ENGINE") == "codex" else "sdk"
    return {"slug": slug, "username": slug, "name": env.get("CC_RESPONDER_NAME", ""), "chat": chat,
            "chat_title": str(groups.get(str(chat), "")) if chat is not None else "",
            "groups": [{"id": int(k), "title": str(v)} for k, v in groups.items() if re.fullmatch(r"-?\d+", k)],
            "cwd": cwd, "git": bool(cwd) and os.path.exists(os.path.join(cwd, ".git")),
            "site": sdk.get("CC_CHAT_SITE", ""), "provider": bots_provider(sdk, engine),
            "model": sdk.get("CC_SDK_MODEL", ""), "engine": engine, "about": bots_read(cdir + "/about.md"),
            "brief": bots_read(cdir + "/brief.md"), "persona": persona if isinstance(persona, dict) else {},
            "keys": [k for k, p in MODEL_PRESETS.items() if p["var"] and keys.get(p["var"])], "base": sdk.get("CC_SDK_BASE_URL", ""),
            "active": bots_service("active", slug), "error": bots_read(cdir + "/error.txt").strip()}


def bots_list(cfg):
    root = os.path.join(cfg.home, ".config/zidos-pager/bots")
    try:
        slugs = sorted(x for x in os.listdir(root) if BOT_SLUG.fullmatch(x) and os.path.isdir(os.path.join(root, x)))
    except OSError:
        slugs = []
    return {"ok": True, "bots": [bots_get(cfg, x) for x in slugs]}


def bots_add(cfg, body):
    token = str(body.get("token") or "").strip()
    if not re.fullmatch(r"\d{3,20}:[A-Za-z0-9_-]{20,100}", token):
        return {"ok": False, "error": "token"}
    tg = TG(token, cfg.api_base)
    try:
        me = tg.get_me() or {}
    except TGError as e:
        return {"ok": False, "error": "token" if re.search(r"HTTP (401|404)", str(e)) else "net"}
    slug = str(me.get("username") or "").lower()
    if not BOT_SLUG.fullmatch(slug):
        return {"ok": False, "error": "slug"}
    cdir, sdir = bots_dirs(cfg, slug)
    if os.path.exists(cdir):
        return {"ok": False, "error": "exists"}
    try:  # a second reader of getUpdates (another install, a webhook) answers 409
        tg.call("getUpdates", {"timeout": 0, "limit": 1, "allowed_updates": []})
    except TGError as e:
        return {"ok": False, "error": "busy" if "HTTP 409" in str(e) else "net"}
    for d in (cdir, sdir):
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    kdir = os.path.join(cfg.home, "zidos-pager-knowledge", slug)  # an empty project folder until the owner picks one, never the home
    os.makedirs(kdir, mode=0o700, exist_ok=True)
    sock = sdir + "/sdk.sock"
    env = {"TG_BOT_TOKEN": token, "TG_ALLOWED_USERS": ",".join(str(i) for i in sorted(cfg.allowed)) or None,
           "CC_ROLE": "responder", "CC_LANG": cfg.lang, "TG_API_BASE": cfg.api_base, "CC_RESPONDER_CHAT": None, "CC_RESPONDER_CWD": kdir,
           "CC_RESPONDER_NAME": bots_clean(me.get("first_name"), 64) or None, "CC_RESPONDER_BRIEF": cdir + "/brief.md",
           "CC_RESPONDER_RATE": "30", "CC_RESPONDER_ENGINE": "sdk", "ZIDOS_PAGER_STATE": sdir + "/state.json",
           "CC_RESPONDER_LOG": sdir + "/responder.jsonl", "CC_SDK_SOCKET": sock, "CC_SDK_ENV": cdir + "/sdk.env"}
    env_update(cdir + "/env", env)
    env_update(cdir + "/sdk.env", {"CC_SDK_SOCKET": sock, "CC_SDK_STATE": sdir + "/sdk-state",
                                   "CC_SDK_KEY_FILE": cdir + "/keys.env", "CC_SDK_KEY_VAR": "ANTHROPIC_API_KEY"})
    for name in ("keys.env", "about.md", "brief.md"):
        bots_write(os.path.join(cdir, name), "")
    bots_write(cdir + "/error.txt", bots_problem(cdir, bots_privacy(me), bots_service("start", slug)))
    return {"ok": True, "slug": slug, "username": slug, "name": bots_clean(me.get("first_name"), 64)}


def bots_save(cfg, body):
    slug = str(body.get("slug") or "")
    cdir, sdir = bots_dirs(cfg, slug)
    if not BOT_SLUG.fullmatch(slug):
        return {"ok": False, "error": "slug"}
    if not os.path.isfile(cdir + "/env"):
        return {"ok": False, "error": "missing"}
    env_up, sdk_up, keys_up, files = {}, {}, {}, {}
    if "cwd" in body:
        cwd = os.path.expanduser(str(body.get("cwd") or "").strip())
        if not cwd or not os.path.isdir(cwd):
            return {"ok": False, "error": "cwd"}
        env_up["CC_RESPONDER_CWD"] = cwd
    if "site" in body:
        site = str(body.get("site") or "").strip()
        if site and not BOT_HTTPS.fullmatch(site):
            return {"ok": False, "error": "site"}
        sdk_up["CC_CHAT_SITE"] = site or None
    if "chat" in body:
        chat = body.get("chat")
        if chat in (None, ""):
            env_up["CC_RESPONDER_CHAT"] = None
        elif isinstance(chat, int) and not isinstance(chat, bool):
            env_up["CC_RESPONDER_CHAT"] = str(chat)
        else:
            return {"ok": False, "error": "chat"}
    if "rate" in body:
        rate = body.get("rate")
        if not isinstance(rate, int) or isinstance(rate, bool) or not 1 <= rate <= 10000:
            return {"ok": False, "error": "rate"}
        env_up["CC_RESPONDER_RATE"] = str(rate)
    for field, name, limit in (("about", "about.md", 8000), ("brief", "brief.md", 20000)):
        if field in body:
            if not isinstance(body[field], str) or len(body[field]) > limit:
                return {"ok": False, "error": field}
            files[name] = body[field]
    persona = None
    if body.get("persona") is not None:
        p = body["persona"]
        if not isinstance(p, dict):
            return {"ok": False, "error": "persona"}
        persona = {k: bots_clean(p.get(k), n) for k, n in PERSONA_LIMITS}
        env_up["CC_RESPONDER_NAME"] = persona["name"] or None
    if bots_model_changes(cfg, slug, body):
        err = bots_model(cfg, slug, body, env_up, sdk_up, keys_up)
        if err:
            return {"ok": False, "error": err}
    # everything validated: write
    if keys_up:
        env_update(cdir + "/keys.env", keys_up)
    if sdk_up:
        env_update(cdir + "/sdk.env", sdk_up)
    if env_up:
        env_update(cdir + "/env", env_up)
    for name, text in files.items():
        bots_write(os.path.join(cdir, name), text)
    error = ""
    if persona is not None:
        saved = bots_read_json(cdir + "/persona.json")
        token = read_env_file(cdir + "/env").get("TG_BOT_TOKEN", "")
        tg = TG(token, cfg.api_base)
        for field, method, param in (("name", "setMyName", "name"), ("short", "setMyShortDescription", "short_description"),
                                     ("description", "setMyDescription", "description")):
            if not persona[field]:
                persona[field] = str(saved.get(field) or "")  # an empty field keeps what is applied
            elif persona[field] != saved.get(field):
                try:
                    tg.call(method, {param: persona[field]})
                except TGError as e:
                    error = error or bots_clean(e, 300).replace(token or "\0", "***")
                    persona[field] = str(saved.get(field) or "")  # not applied: retried by the next save
        bots_write(cdir + "/persona.json", json.dumps(persona, ensure_ascii=False))
    try:
        priv = bots_privacy(TG(read_env_file(cdir + "/env").get("TG_BOT_TOKEN", ""), cfg.api_base).get_me() or {})
    except TGError:
        priv = ""
    error = "; ".join(x for x in (error, priv) if x)
    bots_write(cdir + "/error.txt", bots_problem(cdir, error, bots_service("restart", slug)))
    return {"ok": True, "bot": bots_get(cfg, slug)}


BOTS_REPO = os.path.dirname(os.path.abspath(__file__))


def bots_read_json(path):
    try:
        data = json.loads(bots_read(path) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def bots_privacy(me):
    """getMe of a group bot -> the fix when Telegram hides the group from it (measured 05.11: with privacy mode on,
    plain «@bot question» never reached the bot, only /cmd@bot did)."""
    if me.get("can_read_all_group_messages") is False:
        return ("privacy mode is on, so the bot hears only /commands and replies in the group: send /setprivacy "
                "to @BotFather, choose Disable, then add the bot to the group again")
    return ""


def bots_problem(cdir, error, started):
    """error.txt of a bot: the Telegram error of this call plus what stops the process pair (English, no secrets)."""
    parts = [error] if error else []
    if not started:
        parts.append("services did not start: run python3 setup.py again")
    elif (read_env_file(cdir + "/env").get("CC_RESPONDER_ENGINE") != "codex"
          and not os.path.exists(BOTS_REPO + "/.venv-sdk/bin/python")):
        parts.append("SDK runner is not installed: run python3 setup.py again and choose a model")
    return "; ".join(parts)


def bots_model_changes(cfg, slug, body):
    """True only when the body really changes the model (the mini-app sends provider on every save)."""
    cdir = bots_dirs(cfg, slug)[0]
    sdk = read_env_file(cdir + "/sdk.env")
    cur = bots_provider(sdk, "codex" if read_env_file(cdir + "/env").get("CC_RESPONDER_ENGINE") == "codex" else "sdk")
    pid, model, base = (str(body.get(k) or "").strip() for k in ("provider", "model", "base"))
    return bool(str(body.get("key") or "").strip() or (pid and pid != cur) or (model and model != sdk.get("CC_SDK_MODEL", ""))
                or (base and (pid or cur) == "custom" and base != sdk.get("CC_SDK_BASE_URL", "")))


def bots_model(cfg, slug, body, env_up, sdk_up, keys_up):
    """Provider / model / key of one bot, checked like api_model_save: the key is stored only after model_check == ok."""
    cdir = bots_dirs(cfg, slug)[0]
    sdk = read_env_file(cdir + "/sdk.env")
    cur = bots_provider(sdk, "codex" if read_env_file(cdir + "/env").get("CC_RESPONDER_ENGINE") == "codex" else "sdk")
    pid = str(body.get("provider") or cur or "")
    preset = MODEL_PRESETS.get(pid)
    if preset is None:
        return "provider"
    if preset["base"] is None:  # Codex: its own login on this machine, group answers only
        try:
            if subprocess.run(["codex", "login", "status"], capture_output=True, timeout=20).returncode != 0:
                return "codex"
        except (OSError, subprocess.SubprocessError):
            return "codex"
        env_up["CC_RESPONDER_ENGINE"] = "codex"
        return None
    model = str(body.get("model") or (sdk.get("CC_SDK_MODEL") if pid == cur else "") or preset["model"]).strip()
    base = str(body.get("base") or (sdk.get("CC_SDK_BASE_URL") if pid == cur else "") or "").strip() if pid == "custom" else preset["base"]
    key = str(body.get("key") or "").strip()
    if not _MODEL_NAME.fullmatch(model):
        return "model"
    if pid == "custom" and not BOT_HTTPS.fullmatch(base):
        return "base"
    if key and not _MODEL_KEY.fullmatch(key):
        return "key"
    var = preset["var"]
    key = key or env_read(cdir + "/keys.env").get(var, "")
    if not key:
        return "key"
    verdict = model_check(base, key, model)
    if verdict != "ok":
        return verdict
    keys_up[var] = key
    sdk_up.update({"CC_SDK_KEY_VAR": var, "CC_SDK_MODEL": model, "CC_SDK_BASE_URL": base or None})
    env_up["CC_RESPONDER_ENGINE"] = None
    return None


def bots_llm(base, key, model, system, user):
    """One Anthropic-compatible /v1/messages call -> the answer text. Raises OSError / ValueError."""
    body = json.dumps({"model": bots_alias(base, model), "max_tokens": 8000, "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request((base or "https://api.anthropic.com").rstrip("/") + "/v1/messages", body,
                                 {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict))


def bots_persona(cfg, body):
    slug = str(body.get("slug") or "")
    if not BOT_SLUG.fullmatch(slug):
        return {"ok": False, "error": "slug"}
    cdir = bots_dirs(cfg, slug)[0]
    env, sdk = read_env_file(cdir + "/env"), read_env_file(cdir + "/sdk.env")
    if not env:
        return {"ok": False, "error": "missing"}
    if env.get("CC_RESPONDER_ENGINE") == "codex":
        return {"ok": False, "error": "persona_codex"}
    about = str(body.get("about") or "").strip()
    key = env_read(cdir + "/keys.env").get(sdk.get("CC_SDK_KEY_VAR") or "ANTHROPIC_API_KEY", "")
    if not about or len(about) > 8000:
        return {"ok": False, "error": "about"}
    if not key or not sdk.get("CC_SDK_MODEL"):
        return {"ok": False, "error": "persona_nomodel"}  # no model yet: generation needs the bot's own model and key
    try:
        text = bots_llm(sdk.get("CC_SDK_BASE_URL", ""), key, sdk["CC_SDK_MODEL"], PERSONA_SYSTEM, about)
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        brief = data["brief"]
        persona = {k: bots_clean(data[k], n) for k, n in PERSONA_LIMITS}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):  # ValueError covers JSON and index errors
        return {"ok": False, "error": "persona_fail"}
    if not isinstance(brief, str) or not brief.strip() or not all(persona.values()):
        return {"ok": False, "error": "persona_fail"}
    return {"ok": True, "brief": brief.strip(), "persona": persona}


def bots_remove(cfg, body):
    slug = str(body.get("slug") or "")
    if not BOT_SLUG.fullmatch(slug):
        return {"ok": False, "error": "slug"}
    cdir, sdir = bots_dirs(cfg, slug)
    if not os.path.isdir(cdir):
        return {"ok": False, "error": "missing"}
    bots_service("stop", slug)
    for d in (cdir, sdir):
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


BOTS_ACTIONS = {"add": bots_add, "save": bots_save, "persona": bots_persona, "remove": bots_remove}


def bots_main(cfg, argv):
    """zidos_pager.py bots list|add|save|persona|remove: JSON on stdin, JSON on stdout, exit 0 even on {"ok": false}."""
    action = argv[0] if argv else ""
    if action == "list":
        out = bots_list(cfg)
    elif action in BOTS_ACTIONS:
        try:
            body = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            body = None
        out = BOTS_ACTIONS[action](cfg, body) if isinstance(body, dict) else {"ok": False, "error": "json"}
    else:
        out = {"ok": False, "error": "action"}
    print(json.dumps(out, ensure_ascii=False))
    return 0


def help_default():
    return _t('Control Claude Code sessions from your phone.\n\n🎛 The panel is one message with the session list, like the claude agents screen. A number opens a session (a card with buttons). The /s command recreates the panel.\n\nPlain text goes to the current session (marked ▶). To write to another one without switching, reply (swipe) to any of its messages. Model replies arrive while a turn started by your message here is running.\n\n🔔 Notifications are off by default: press «🔔 Follow» in a session card — you will get «waiting for you», «done», «error». Sessions started from here are already followed.\n\n❓ Questions with options arrive as buttons: pick an option or «✏️ Your own answer». While a question is open, plain text is not sent to the session.\n\nCommands: /s /new /quota /help')


def help_topics():
    return _t('How to use\n\n• Tap a session in the panel — its topic opens.\n• Write in the session topic like in a chat, the reply comes back there.\n• Text written here goes to a fitting session or starts a new session.\n• Questions with options arrive as buttons.\n• 🔔 in the session card — notifications about it.\n• Topic icon: 🟡 waiting · ❓ question · 🔵 running · ✅ done.\n• Topics of finished sessions are removed automatically after a day.')


def help_sdk():
    return _t('Control Claude sessions from your phone (SDK mode: no Claude Code on this machine).\n\n🎛 The panel is one message with the session list. A number opens a session (a card with buttons). The /s command recreates the panel.\n\nPlain text goes to the current session (marked ▶). To write to another one without switching, reply (swipe) to any of its messages. Model replies arrive while a turn started by your message here is running.\n\n🔐 When a session wants to run a tool, a message with «Allow», «Always» and «Deny» buttons arrives. ❓ Questions with options arrive as buttons too. 💲 The cost of a session is shown in its card; at the budget limit press «+1 $ and continue».\n\n🔔 Notifications are off by default: press «🔔 Follow» in a session card. Sessions started from here are already followed.\n\nCommands: /s /new /help')


def no_backend_text():
    return _t("No Claude Code and the SDK runner does not respond: install one of the two (see README).")


def hint_main():
    return _t("The panel is in the topic «%s». Write to the topic of the session you need.") % panel_title()


def no_session():
    return _t("First pick a session: press «📋 Sessions».")


def err_list():
    return _t("Could not get the session list. Try again in a couple of seconds.")


class Bot:
    def __init__(self, cfg, tg=None, runner=default_runner, tmux=None, spawn=None,
                 sleep=time.sleep, clock=time.time):
        self.cfg = cfg
        self.tg = tg or TG(cfg.token, cfg.api_base)
        self.run = self._claude_guard(runner)
        self._claude_chk = (None, False)  # (checked at, binary is missing)
        self._claude_logged = None
        self.tmux = tmux or Tmux(cfg.claude_bin, runner)
        self.tmux.herdr = HerdrClient(cfg.herdr_socket, cfg.herdr_mode)
        self.tmux.sdk = SdkClient(cfg.sdk_socket, cfg.sdk_mode)
        self._sdk_logged = 0.0
        self._sdk_ready = (0.0, False)  # (checked at, runner alive and has a key)
        self._sdk_lock = threading.Lock()
        self.stt = SttClient(cfg.stt_socket, cfg.stt_mode)
        self._stt_logged = 0.0
        self._stt_ready = (0.0, False)  # (checked at, runner alive and has a ready engine)
        self._notify_lock = threading.Lock()
        self._hd_seen = {}      # sid -> (revision, first seen, last change)
        self._hd_cache = {}     # sid -> (revision, read at, detail line)
        self._hd_status = {}    # pane id -> last status seen in the event stream
        self._hd_logged = 0.0
        self.store = Store(cfg.state_path)
        self.spawn = spawn or self._spawn
        self.sleep = sleep
        self.clock = clock
        self.known = {}
        self.primed = False
        self.injector = self.tmux.send_text
        self.stop_event = threading.Event()
        self.last_forward = {}
        self.reply_to = {}
        self.status = {}
        self.topics_enabled = False
        self._plock = threading.RLock()
        self._ask_cache = {}
        self.names_busy = False
        self.last_full = None
        self._sub_cache = {}
        self.login_attempts = {}
        self._vapid = None
        self.webapp_touch = {}
        self.chat_username = None  # the @name the shared chat addresses (the chat bot, or this bot without one)
        # the shared chat may have its own bot (CC_RESPONDER_TOKEN); the owner's 🔐 always come from the console bot
        self.tg_chat = TG(cfg.responder_token, cfg.api_base) if cfg.responder_token else self.tg
        self._resp_lock = threading.RLock()
        self._resp_queue = []    # questions waiting for the responder session, oldest first
        self._resp_pulled = 0.0  # when the chat clone was last brought up to date
        self._resp_pull_told = False  # the owner already knows the clone is stuck
        self._resp_busy = None   # the question being answered
        self._resp_times = []    # when questions were accepted (the hourly limit)
        self._perm_cache = {}   # tmux sid -> {"perm", "sig", "pushed"}: filled by the background tick
        self._perm_msgs = self.perm_msgs_load()  # (tmux sid, chat) -> the Telegram notice of a dialog: {"mid", "sig", "head"}
        self._perm_answered = {}  # tmux sid -> (sig, expiry) of a dialog we have just answered
        self._perm_opened = {}    # tmux sid -> when perm_open last opened a window for it
        self._perm_prime = set()  # tmux sids whose window was opened on the first tick: their dialog is not news
        self._perm_pushed = set()  # (sid, req) of SDK requests already pushed
        self.last_forward_text = {}
        self.turn_forwarded = set()
        self.api_lock = threading.RLock()
        self._rewidth_at = {}  # sid -> when the mini-app screen last re-rendered it
        self._hist = {}
        self.webapp_url_cur = None
        self.webapp_menu_initialized = False
        self.webapp_server = None
        self.temp = []
        self._temp_lock = threading.Lock()
        self._route_lock = threading.Lock()  # All-feed messages are routed one by one
        self._cbl = threading.local()  # the button press being handled in this thread (see popup)
        self.topic_deleted = []
        self.tg.on_message = self._log_msg

    @staticmethod
    def _spawn(fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ---- sending and remembering which session a message belongs to
    def remember(self, chat, results, sid):
        if not sid:
            return
        mm = self.store.data.setdefault("msgmap", {})
        for r in results:
            if isinstance(r, dict) and r.get("message_id"):
                mm["%s:%s" % (chat, r["message_id"])] = sid
        while len(mm) > 1000:
            del mm[next(iter(mm))]
        self.store.save()

    def _th(self, chat, sid):
        if not (self.topics_enabled and sid):
            return None
        t = self.store.chat(chat)["topics"].get(sid)
        return t["thread"] if t else None

    def _threaded(self, chat, sid, fn):
        th = self._th(chat, sid)
        try:
            return fn(th)
        except TGError as e:
            if th and "thread not found" in str(e).lower():
                self.store.chat(chat)["topics"].pop(sid, None)  # the topic was deleted manually
                self.store.save()
                return fn(None)
            raise

    def _log_msg(self, chat, res, thread):
        if thread is None and res.get("message_id"):
            now = self.clock()
            log_ = self.store.data.setdefault("msglog", [])
            log_.append([chat, res["message_id"], now])
            while log_ and (now - log_[0][2] > LOG_TTL or len(log_) > 5000):
                log_.pop(0)

    def say(self, chat, text, markup=None, sid=None, reply_to=None, silent=False):
        res = self._threaded(chat, sid, lambda th: self.tg.send(chat, text, markup, reply_to, thread=th, silent=silent))
        self.remember(chat, res, sid)
        return res[-1] if res else None

    def say_md(self, chat, text, header=None, sid=None, reply_to=None, doc_name="answer.md", silent=False):
        res = self._threaded(chat, sid, lambda th: self.tg.send_md(
            chat, text, header=header, reply_to=reply_to, doc_name=doc_name, thread=th, silent=silent))
        self.remember(chat, res, sid)
        return res[-1] if res else None

    def say_screen(self, chat, text, sid):
        r = self._threaded(chat, sid, lambda th: self.tg.send_rich(
            chat, "<pre>%s</pre>" % esc(text), text, screen_markup(sid), thread=th))
        self.remember(chat, [r], sid)

    def say_temp(self, chat, text, markup=None, sid=None, ttl=TEMP_TTL, reply_to=None):
        r = self.say(chat, text, markup, sid=sid, reply_to=reply_to)
        if isinstance(r, dict) and r.get("message_id"):
            with self._temp_lock:
                self.temp.append((self.clock() + ttl, chat, r["message_id"]))
        return r

    def popup(self, chat, text, sid=None, alert=False):
        """A short answer to a button press: shown by Telegram over the chat, not written into it.
        Outside a button press (or when the press is already answered) it falls back to a temporary message."""
        cb = getattr(self._cbl, "cur", None)
        if cb and not cb["done"]:
            cb["done"] = True
            if self.tg.answer(cb["id"], text, alert):
                return True
        if alert:  # an error must not vanish: a plain message that stays
            self.say(chat, text, sid=sid)
        else:
            self.say_temp(chat, text, sid=sid)
        return False

    def cb_finish(self, cb, text=None):
        if not cb["done"]:
            cb["done"] = True
            self.tg.answer(cb["id"], text)

    def spawn_cb(self, fn, *args):
        """Runs a button handler in the background; the press is answered when it ends (or by a popup)."""
        cb = self._cbl.cur
        cb["deferred"] = True
        self.spawn(self._run_cb, cb, fn, *args)

    def _run_cb(self, cb, fn, *args):
        prev = getattr(self._cbl, "cur", None)
        self._cbl.cur = cb
        try:
            fn(*args)
        finally:
            self._cbl.cur = prev
            self.cb_finish(cb)

    def single(self, chat, kind, text, markup=None, sid=None):
        """A service message of the main chat: a new one deletes the previous one of the same kind."""
        singles = self.store.data.setdefault("singles", {})
        key = "%s:%s" % (chat, kind)
        prev = singles.get(key)
        r = self.say(chat, text, markup, sid=sid)
        mid = r.get("message_id") if isinstance(r, dict) else None
        if mid:
            singles[key] = mid
            self.store.save()
        if prev and prev != mid:
            self.tg.delete(chat, prev)
        return r

    def temp_tick(self):
        now = self.clock()
        with self._temp_lock:
            due = [t for t in self.temp if t[0] <= now]
            self.temp = [t for t in self.temp if t[0] > now]
        for _, chat, mid in due:
            self.tg.delete(chat, mid)

    # ---- topics
    def session_emoji(self, sid):
        if self.is_old(sid):
            return "🗂"
        if self.pending_question(sid):
            return "❓"
        return EMOJI.get(self.session_info(sid)["state"], "⚪")

    def topic_title(self, sid):
        # No state emoji: every topic rename is written by Telegram as a service message
        # into the shared feed, and the state changes after every turn. The state is visible in the panel and the mini-app.
        return self.session_name(sid)[:128]

    def sid_of_thread(self, chat, thread):
        for sid, t in self.store.chat(chat)["topics"].items():
            if t["thread"] == thread:
                return sid
        return None

    def touch_topic(self, chat, sid):
        t = self.store.chat(chat)["topics"].get(sid)
        if t:
            t["activity"] = self.clock()

    def tracked_c(self, c, sid):
        t = c["topics"].get(sid)
        return c["session"] == sid or self.is_watched(sid) or bool(t and not t.get("perm_only"))

    def ensure_topic(self, chat, sid, perm_only=False):
        """perm_only: a topic made only for permission requests of a session that is not subscribed to —
        it does not subscribe (no forwarding, no other notifications) until the owner opens it."""
        if not self.topics_enabled or self.is_archived(sid):
            return None
        c = self.store.chat(chat)
        t = c["topics"].get(sid)
        if t:
            if not perm_only:
                t.pop("perm_only", None)
                self.mark_scan(c, sid)
            return t["thread"]
        title = self.topic_title(sid)
        try:
            thread = self.tg.create_topic(chat, title)
        except TGError as e:
            log("createForumTopic failed: %s" % e)
            return None
        now = self.clock()
        c["topics"][sid] = {"thread": thread, "title": title, "last_edit": now, "activity": now}
        if perm_only:
            c["topics"][sid]["perm_only"] = True
        else:
            self.mark_scan(c, sid)
        self.store.save()
        return thread

    def open_in_topic(self, chat, sid):
        if self.ensure_topic(chat, sid) is None:
            self.make_current(chat, sid)
            self.mark_scan(self.store.chat(chat), sid)
            self.send_card(chat, sid)
            return
        self.mark_scan(self.store.chat(chat), sid)
        self.send_card(chat, sid)
        self.panel_flash(chat, _t("→ opened in topic «%s»") % self.session_name(sid), ttl=20)

    def topics_tick(self):
        if not self.topics_enabled:
            return
        now = self.clock()
        for uid in self.cfg.allowed:
            for sid, t in list(self.store.chat(uid)["topics"].items()):
                title = self.topic_title(sid)
                if title == t["title"] or now - t["last_edit"] < TOPIC_EDIT_MIN:
                    t.pop("want", None)
                    continue
                if t.get("want") != title:  # automatic names change in quick steps: wait for the last one
                    t["want"], t["want_at"] = title, now
                    continue
                if now - t.get("want_at", now) < TOPIC_RENAME_SETTLE:
                    continue
                t.pop("want", None)
                try:
                    self.tg.edit_topic(uid, t["thread"], title)
                except TGError as e:
                    if "not modified" not in str(e).lower():
                        log("editForumTopic failed: %s" % e)
                t["title"], t["last_edit"], t["activity"] = title, now, now
            self.store.save()

    def drop_topic(self, chat, sid, delete=True):
        c = self.store.chat(chat)
        t = c["topics"].pop(sid, None)
        for k in [k for k in c["cards"] if k.endswith(":" + sid) and (not t or k.split(":")[0] == str(t["thread"]))]:
            c["cards"].pop(k, None)
        c["notes"].pop(sid, None)
        if t and delete:
            try:
                self.tg.delete_topic(chat, t["thread"])
            except TGError as e:
                log("deleteForumTopic failed: %s" % e)
        if not self.is_watched(sid) and c["session"] != sid:
            c["fw"].pop(sid, None)
        self.status.pop((chat, sid), None)
        self.store.save()

    def topic_cleanup(self):
        """Self-cleaning: session gone or stopped -> delete the topic; done/failed with no activity longer than the TTL -> delete."""
        if not self.topics_enabled:
            return
        sessions = self.list_sessions(show_all=True)
        if sessions is None:
            return
        ids = {x["id"] for x in sessions}
        now = self.clock()
        ttl = self.cfg.topic_ttl_hours * 3600
        for uid in self.cfg.allowed:
            topics = self.store.chat(uid)["topics"]
            if not ids and topics:
                continue  # an empty answer while topics exist is probably a glitch — do not touch anything
            for sid, t in list(topics.items()):
                gone = sid not in ids and (is_sdk(sid) or is_herdr(sid) or not self.claude_missing())  # no claude: no list
                if gone or (self.stopped(sid) and not self.pending_question(sid)):
                    self.drop_topic(uid, sid)
                    continue
                old = self.is_old(sid)
                if self.pending_question(sid) and not old:
                    continue
                if (old or self.session_info(sid)["state"] in ("done", "failed")) and now - t["activity"] > ttl:
                    self.drop_topic(uid, sid)

    # ---- tracker, quota, cleanup
    # ---- panel: one self-updating message with modes
    def panel_state(self, chat):
        p = self.store.data.setdefault("panel", {}).setdefault(str(chat), {})
        for k, v in (("thread", None), ("mid", None), ("key", ""), ("show_all", False), ("mode", "list"),
                     ("mode_at", 0), ("flash", None)):
            p.setdefault(k, v)
        return p

    def panel_thread(self, chat):
        return self.panel_state(chat)["thread"] if self.topics_enabled else None

    def panel_mode(self, chat, mode):
        p = self.panel_state(chat)
        p["mode"], p["mode_at"] = mode, self.clock()
        if mode == "list":
            self.store.chat(chat)["pending_task"] = None

    def panel_flash(self, chat, text, ttl=60):
        p = self.panel_state(chat)
        p["flash"] = {"text": text, "until": self.clock() + ttl}
        p["mode"], p["mode_at"] = "list", self.clock()
        self.panel_publish(chat)

    def session_ask(self, s):
        """(question, [options]) for a session with a pending AskUserQuestion, or None."""
        parts = self.ask_parts(s)
        return (parts[0]["question"], [o["label"] for o in parts[0]["options"]]) if parts else None

    def ask_parts(self, s):
        """(current question dict, step, number of questions) of a pending AskUserQuestion, or None."""
        q = self.pending_question(s["id"])
        if q:
            step = min(q["step"], len(q["questions"]) - 1)
            return q["questions"][step], step, len(q["questions"])
        if s["state"] != "working":
            return None
        path = self.transcript_path(s["id"])
        try:
            st = os.stat(path)
            key = (st.st_size, st.st_mtime)
        except (OSError, TypeError):
            return None
        cached = self._ask_cache.get(s["id"])
        if cached and cached[0] == key:
            ev = cached[1]
        else:
            ev = unanswered_ask(path, tail_bytes=256 * 1024)
            self._ask_cache[s["id"]] = (key, ev)
        if not ev:
            return None
        return ev["questions"][0], 0, len(ev["questions"])

    def stopped(self, sid):
        """Stopped by the owner or killed (the last listing): its topic is not kept for a day like a finished one."""
        k = self.known.get(sid) or {}
        return k.get("state") == "stopped" or k.get("sdk_state") == "stopped"

    def done_topics(self, chat):
        return [sid for sid in self.store.chat(chat)["topics"]
                if self.is_old(sid) or self.stopped(sid) or (self.session_info(sid)["state"] in ("done", "failed")
                                        and not self.pending_question(sid))]

    def recent_dirs(self, cached=False):
        dirs = [self.cfg.default_cwd]
        src = (sorted(self.known.values(), key=lambda x: -x.get("updated", 0)) if cached
               else (self.list_sessions(True) or []))
        for x in src:
            if x["cwd"] and x["cwd"] not in dirs:
                dirs.append(x["cwd"])
            if len(dirs) >= 6:
                break
        return dirs[:6]

    def panel_render(self, chat):
        """-> (html, plain, markup) for the current mode, or None if the session list is unavailable."""
        p = self.panel_state(chat)
        c = self.store.chat(chat)
        now = self.clock()
        mode = p["mode"]
        bold = lambda t: (t, "<b>%s</b>" % esc(t))
        txt = lambda t: (t, esc(t))

        def build(lines, rows):
            return ("\n".join(h for _, h in lines), "\n".join(pl for pl, _ in lines), {"inline_keyboard": rows})
        back = [{"text": _t("◀ Back"), "callback_data": "pm:list"}]
        if mode == "quota":
            ls = quota_detail(read_quota(self.cfg.rate_file), now).split("\n")
            return build([bold(ls[0])] + [txt(x) for x in ls[1:]], [back])
        if mode == "help":
            return build([txt(self.help_text())], [[{"text": _t("📱 App"), "callback_data": "pwa"}], back])
        if mode == "arch":
            n = len(self.done_to_archive())
            if not n:
                return build([bold(_t("Archive done")), txt(_t("No finished sessions."))],
                             [[{"text": _t("◀ Back"), "callback_data": "pa:1"}]])
            names = [txt("• " + short(self.display_name(sid), 40)) for sid in self.done_to_archive()[:12]]
            return build([bold(_t("Archive done")),
                          txt(_t("Move %d finished sessions to the archive? Conversations are kept, "
                                 "topics will be closed.") % n)] + names,
                         [[{"text": _t("Yes, archive"), "callback_data": "pay"},
                           {"text": _t("✖ Cancel"), "callback_data": "pa:1"}]])
        if mode == "clean":
            n = len(self.done_topics(chat))
            if not n:
                return build([bold(_t("Remove finished topics")), txt(_t("No topics of finished sessions."))], [back])
            return build([bold(_t("Remove finished topics")),
                          txt(_t("Topics to delete: %d. The sessions themselves stay.") % n)],
                         [[{"text": _t("Yes, remove"), "callback_data": "pcy"},
                           {"text": _t("✖ Cancel"), "callback_data": "pm:list"}]])
        if mode == "names":
            return self.names_view(p, build, bold, txt, back)
        if mode == "engine":
            rows = [[{"text": _t("▶ Subscription (Claude Code)"), "callback_data": "neg:sub"}],
                    [{"text": _t("⚡ SDK (API key)"), "callback_data": "neg:sdk"}],
                    [{"text": _t("✖ Cancel"), "callback_data": "ncx"}]]
            return build([bold(_t("How to start?"))], rows)
        pt = c.get("pending_task")
        if mode in ("new", "dirs") and pt:
            lines = [bold(_t("New session")), txt("«%s»" % short(pt["text"], 200))]
            if mode == "new":
                lines.append(txt(_t("→ folder %s") % tilde(pt["cwd"])))
                rows = [[{"text": _t("▶ Start"), "callback_data": "ngo"}]]
                if self.sdk_ready() and not self.claude_missing():
                    rows.append([{"text": _t("⚡ Via SDK (API key)"), "callback_data": "ngs"}])
                return build(lines, rows + [[{"text": _t("📁 Other folder"), "callback_data": "ndl"}],
                                            [{"text": _t("✖ Cancel"), "callback_data": "ncx"}]])
            lines.append(txt(_t("Choose a folder:")))
            rows = [[{"text": "📁 " + tilde(d), "callback_data": "nt:%d" % i}]
                    for i, d in enumerate(c["new_dirs"])]
            rows.append([{"text": _t("◀ Back"), "callback_data": "nb"}, {"text": _t("✖ Cancel"), "callback_data": "ncx"}])
            return build(lines, rows)
        if self.claude_missing() and not self.sdk_ready():
            return build([bold(no_backend_text())], [[{"text": btn("list"), "callback_data": "pm:list"},
                                                      {"text": _t("❓ Help"), "callback_data": "pm:help"}]])
        # ---- list (for mobile Telegram: the name is a button, the text has the name and one "what next" line)
        sessions = self.list_sessions(True)
        if sessions is None:
            return None
        perms, waiting, working, done, old, arch = [], [], [], [], [], []
        for x in sessions:
            if self.is_archived(x["id"]):  # a bot mark: not counted as waiting, working or in any counter
                arch.append((x, None))
                continue
            if self.is_old(x["id"]):  # superseded by a continuation — do not show among the active ones
                old.append((x, None))
                continue
            if self.perm_flag(x["id"], x["state"]):  # a permission request blocks the work: its own group on top
                perms.append((x, None))
                continue
            ask = self.session_ask(x) if x["state"] in ("working", "blocked") else None
            if x["state"] == "blocked" or ask:
                waiting.append((x, ask))
            elif x["state"] in ("done", "failed"):
                done.append((x, None))
            else:
                working.append((x, None))
        for grp in (perms, waiting, working, done, old):
            grp.sort(key=lambda t: -t[0]["updated"])
        arch.sort(key=lambda t: -(self.archived_at(t[0]["id"]) or 0))
        counts = []
        if perms:
            counts.append("🔐 %d" % len(perms))
        if waiting:
            counts.append(_t("🟡 %d waiting") % len(waiting))
        if working:
            counts.append(_t("🔵 %d working") % len(working))
        if done:
            counts.append(_t("✅ %d done") % len(done))
        if old and p.get("show_all"):  # in the normal view the number is shown on the "Archive" button
            counts.append(_t("🗂 %d superseded") % len(old))
        if arch and p.get("show_all"):
            counts.append(_t("📦 %d archived") % len(arch))
        lines = []
        fl = p.get("flash")
        if fl and fl["until"] > now:
            lines += [bold(fl["text"]), txt("")]
        lines.append(bold(" · ".join(counts) if counts else _t("No sessions")))
        if not self.claude_missing():  # the subscription quota exists only with Claude Code
            lines.append(txt(quota_short(read_quota(self.cfg.rate_file), now)))
        watched = set(self.store.data.get("watched", []))
        topics = c["topics"]
        show_done = bool(p.get("show_all"))
        page = int(p.get("page") or 0)
        if show_done:
            groups = [(_t("Superseded"), old), (_t("Archived"), arch), (_t("Done"), done)]
        else:
            groups = [(_t("🔐 Needs permission"), perms), (_t("Waiting for you"), waiting), (_t("Working"), working)]
        flat = [(title, x, ask) for title, grp in groups for x, ask in grp]
        per = PANEL_PAGE
        pages = max(1, (len(flat) + per - 1) // per)
        page = min(page, pages - 1)
        chunk = flat[page * per:(page + 1) * per]
        shown_title = None
        rows = []
        for title, x, ask in chunk:
            if title != shown_title:
                lines += [txt(""), bold(title.upper())]
                shown_title = title
            emoji = "❓" if ask else ("🔴" if x["state"] == "failed" else EMOJI.get(x["state"], "⚪"))
            if title == _t("🔐 Needs permission"):
                emoji = "🔐"
            elif title == _t("Superseded"):
                emoji = "🗂"
            elif title == _t("Archived"):
                emoji = "📦"
            marks = ("  🔔" if x["id"] in watched else "")
            if emoji == "🔐":
                detail = self.perm_line(x["id"])
            elif ask:
                detail = _t("question: %s") % ask[0]
            elif title == _t("Superseded"):
                kid = self.chain_child(x["id"])
                detail = _t("→ continued in «%s»") % short_keep_tag(self.display_name(kid), 30) if kid else _t("marked as old")
            elif title == _t("Archived"):
                detail = _t("archived · %d d") % max(0, int((now - (self.archived_at(x["id"]) or now)) // 86400))
            else:
                detail = humanize_detail(x["detail"] or x["result"] or "")
            when = "" if title == _t("Archived") else ago_short(x["updated"], now)
            k = self.chain_k(x["id"]) if title not in (_t("Superseded"), _t("Archived")) else 0
            name = short_keep_tag(x["name"], 34)
            sign = emoji + " " if emoji in ("❓", "🔴", "🔐") else ""
            lines.append(("%s%s%s" % (sign, name, marks), "%s<b>%s</b>%s" % (sign, esc(name), esc(marks))))
            sub = " · ".join(v for v in (short(detail, 64), when) if v)
            if k >= 2:
                sub = _t("↪ current · %s") % sub if sub else _t("↪ current")
            if sub:
                lines.append((sub, "<i>%s</i>" % esc(sub)))
            cur = (not self.topics_enabled) and x["id"] == c["session"]
            label = short_keep_tag(x["name"], 30)
            rows.append([{"text": ("▶ " if cur else "") + (label if label.startswith("🗂 ") else "%s %s" % (emoji, label))
                          + (" 💬" if x["id"] in topics else ""),
                          "callback_data": "s:" + x["id"]}])
        if not chunk:
            lines += [txt(""), txt(_t("The archive is empty.") if show_done else _t("Nothing is waiting or running."))]
        if pages > 1:
            nav = []
            if page > 0:
                nav.append({"text": _t("◀ Back"), "callback_data": "pp:%d" % (page - 1)})
            nav.append({"text": "%d / %d" % (page + 1, pages), "callback_data": "pp:%d" % page})
            if page < pages - 1:
                nav.append({"text": _t("More ▶"), "callback_data": "pp:%d" % (page + 1)})
            rows.append(nav)
        if show_done:
            rows.append([{"text": _t("◀ To active"), "callback_data": "pa:0"},
                         {"text": _t("🧹 Remove topics"), "callback_data": "pm:clean"}])
            if done:
                rows.append([{"text": _t("🗂 Archive all done"), "callback_data": "pm:arch"}])
        else:
            # "🗂 Archive 45" (10 characters) gets cut in a row of 4 buttons — two rows of two
            sdk_only = self.claude_missing()
            rows.append([{"text": _t("🗂 Archive %d") % (len(done) + len(old) + len(arch)), "callback_data": "pa:1"}]
                        + ([] if sdk_only else [{"text": _t("📊 Quota"), "callback_data": "pm:quota"}]))
            rows.append(([] if sdk_only else [{"text": _t("✏️ Names"), "callback_data": "pm:names"}])
                        + [{"text": _t("❓ Help"), "callback_data": "pm:help"}])
        if not self.topics_enabled:
            rows.append([{"text": btn("new"), "callback_data": "new"}])
        elif not show_done:
            rows.append([{"text": _t("🆕 New session"), "callback_data": "new"}])
        armed = self.engine_armed(c) if self.topics_enabled else None
        if armed and not show_done:
            lines += [txt(""), bold(_t("Next text in this topic starts a session (%s).")
                                    % (_t("⚡ SDK") if armed == "sdk" else _t("▶ subscription")))]
            rows.append([{"text": _t("✖ Cancel"), "callback_data": "ncx"}])
        elif not show_done and self.topics_enabled:
            lines += [txt(""), (_t("Write a task here — I'll start a new session."),
                                _t("<i>Write a task here — I'll start a new session.</i>"))]
        if self.webapp_url_cur:
            rows.insert(0, [{"text": _t("📋 Open list"),
                             "web_app": {"url": self.webapp_url_cur.rstrip("/") + "/app/"}}])
        return build(lines, rows)

    def panel_publish(self, chat, force_new=False):
        with self._plock:
            p = self.panel_state(chat)
            view = self.panel_render(chat)
            if view is None:
                if p.get("mid"):
                    return
                html = plain = _t("Could not get the session list. The panel will refresh automatically.")
                markup = None
            else:
                html, plain, markup = view
            key = html + "\0" + json.dumps(markup, sort_keys=True)
            if force_new and p.get("mid"):
                self.tg.delete(chat, p["mid"])
                p["mid"] = None
            if p.get("mid") and p.get("key") == key:
                return
            for _ in range(2):
                try:
                    if p.get("mid"):
                        try:
                            self.tg.edit(chat, p["mid"], plain, markup, html=html)
                            p["key"] = key
                            self.store.save()
                            return
                        except TGError as e:
                            if "not found" not in str(e).lower():
                                raise
                            if "thread" in str(e).lower():
                                p["thread"] = None
                            p["mid"] = None
                    if self.topics_enabled and not p.get("thread"):
                        try:
                            p["thread"] = self.tg.create_topic(chat, panel_title())
                        except TGError as e:
                            log("createForumTopic (panel) failed: %s" % e)
                    r = self.tg.send_rich(chat, html, plain, markup, thread=p.get("thread"))
                    p["mid"], p["key"] = r["message_id"], key
                    try:
                        self.tg.pin(chat, p["mid"])
                    except TGError as e:
                        log("pin failed: %s" % e)
                    self.store.save()
                    return
                except TGError as e:
                    if "thread not found" in str(e).lower() and p.get("thread"):
                        p["thread"], p["mid"] = None, None
                        continue
                    raise

    # ---- handoff continuation chains
    def chains_scan(self):
        sessions = self.list_sessions(True)
        if sessions is None:
            return
        if self.store.data.get("chain_scan_ver") != 2:  # the handoff file name rules were extended — re-read the logs
            self.store.data["chain_scan"] = {}
            self.store.data["chain_scan_ver"] = 2
        scan = self.store.data.setdefault("chain_scan", {})
        writers = {}
        for x in sessions:
            sid = x["id"]
            path = x.get("link") or fallback_transcript_path(x["cwd"], x["sessionId"])
            ent = scan.setdefault(sid, {"offset": 0, "writes": {}})
            if path:
                # logs can be 200 MB: read to the end in a single pass (lines are filtered by substring)
                for _ in range(64):
                    before = ent["offset"]
                    new, ent["offset"] = scan_handoff_writes(path, ent["offset"], max_bytes=64 * 1024 * 1024)
                    for fp, ts in new.items():
                        if fp not in ent["writes"] or ts < ent["writes"][fp]:
                            ent["writes"][fp] = ts
                    if ent["offset"] == before:
                        break
        for sid, ent in scan.items():
            for fp, ts in ent["writes"].items():
                writers.setdefault(fp, []).append((ts, sid))
        self.store.data["chain_links"] = self.build_chain_links(sessions, writers)
        self.store.save()

    @staticmethod
    def chain_would_cycle(links, child, parent):
        """The candidate parent is already a descendant of the child (or the child itself) — the link would close a loop."""
        seen = set()
        cur = parent
        while cur and cur not in seen:
            if cur == child:
                return True
            seen.add(cur)
            cur = links.get(cur)
        return False

    def original_name(self, x):
        rec = self.store.data.get("names", {}).get(x["id"]) or {}
        return strip_chain_label(rec.get("source_name") or x.get("real_name") or x["name"])

    def build_chain_links(self, sessions, writers):
        """Priority: writer of the handoff file > numbered copy "X (N)" > line by handoff-file folder."""
        home = self.cfg.home.rstrip("/")
        generic = {os.path.normpath(d) for d in (home, "/tmp", "/var/tmp", "/", *self.cfg.chain_generic_dirs)}
        order = sorted(sessions, key=lambda x: (x["created"], x["id"]))
        links = {}

        by_id = {x["id"]: x for x in sessions}

        def spinoff(child, parent):
            """The «parent» kept working well after the child started: a parallel project, not a continuation."""
            p, c = by_id.get(parent), by_id.get(child)
            return bool(p and c and p.get("state") in ("working", "blocked")
                        and (p.get("updated") or 0) > (c.get("created") or 0) + CHAIN_SPINOFF_GRACE)

        def assign(child, parent, strict=True):
            if (parent and parent != child and not self.chain_would_cycle(links, child, parent)
                    and not (strict and spinoff(child, parent))):
                links[child] = parent
                return True
            return False
        # 1) the parent wrote the file the child refers to
        for x in order:
            for fp in handoff_paths(x.get("task"), home):
                cands = [(ts, w) for ts, w in writers.get(fp, []) if w != x["id"] and ts < x["created"]]
                if cands and assign(x["id"], max(cands)[1]):
                    break
        # 2) Claude Code copies with a number in the original name: "X (2)" continues "X", "X (3)" — "X (2)"
        names = {}
        for x in order:
            names.setdefault(" ".join(self.original_name(x).lower().split()), []).append(x)
        for x in order:
            if x["id"] in links:
                continue
            m = re.match(r"^(.*?)\s*\((\d+)\)$", " ".join(self.original_name(x).lower().split()))
            if not m or int(m.group(2)) < 2:
                continue
            base, n = m.group(1).strip(), int(m.group(2))
            for key in ["%s (%d)" % (base, k) for k in range(n - 1, 1, -1)] + [base]:
                cands = [y for y in names.get(key, []) if y["id"] != x["id"] and y["created"] <= x["created"]]
                if cands:
                    assign(x["id"], max(cands, key=lambda y: (y["created"], y["id"]))["id"], strict=False)
                    break
        # 3) one line: handoff files in one (not too generic) folder; the parent is the nearest earlier one
        folders = {}
        for x in order:
            for fp in handoff_paths(x.get("task"), home):
                d = os.path.dirname(fp)
                if d not in generic:
                    folders.setdefault(d, []).append(x)
        for x in order:
            if x["id"] in links:
                continue
            cands = [y for d, group in folders.items() if x in group for y in group
                     if y["id"] != x["id"] and y["created"] < x["created"]]
            if cands:
                assign(x["id"], max(cands, key=lambda y: (y["created"], y["id"]))["id"])
        return links
    def chain_parent(self, sid):
        return self.store.data.get("chain_links", {}).get(sid)

    def chain_children(self, sid):
        return [c for c, p in self.store.data.get("chain_links", {}).items() if p == sid]

    def chain_child(self, sid):
        """The latest direct child (by creation time) or None."""
        kids = self.chain_children(sid)
        if not kids:
            return None
        return max(kids, key=lambda k: (self.known.get(k, {}).get("created", 0), k))

    def chain_k(self, sid):
        """Position in the chain: depth + 1 (1 is the first session)."""
        k, seen = 1, {sid}
        while True:
            parent = self.chain_parent(sid)
            if not parent or parent in seen:
                return k
            seen.add(parent)
            sid = parent
            k += 1

    def chain_head(self, sid):
        seen = {sid}
        while True:
            kid = self.chain_child(sid)
            if not kid or kid in seen:
                return sid
            seen.add(kid)
            sid = kid

    def is_old(self, sid):
        """Superseded (has a child) unless there is a manual mark; a manual mark beats automation."""
        manual = self.store.data.get("chain_manual", {}).get(sid)
        if manual in ("old", "current"):
            return manual == "old"
        return bool(self.chain_children(sid))

    def name_proposals(self):
        return [(sid, rec.get("source_name") or sid, rec["name"])
                for sid, rec in self.store.data.get("names", {}).items()
                if rec.get("name") and not rec.get("skip") and not rec.get("approved")]

    def names_view(self, p, build, bold, txt, back):
        if self.names_busy:
            return build([bold(_t("New names")), txt(_t("Thinking up names…"))], [back])
        auto = bool(self.store.data.get("auto_names"))
        auto_btn = [{"text": _t("🔁 Auto: on") if auto else _t("🔁 Auto: off"), "callback_data": "nm:auto"}]
        items = self.name_proposals()
        if not items:
            return build([bold(_t("New names")), txt(_t("All names are already clear."))], [auto_btn, back])
        pages = (len(items) + PANEL_NAMES_PAGE - 1) // PANEL_NAMES_PAGE
        page = min(int(p.get("npage") or 0), pages - 1)
        chunk = items[page * PANEL_NAMES_PAGE:(page + 1) * PANEL_NAMES_PAGE]
        lines = [bold(_t("New names")), txt(_t("Suggested: %d") % len(items)), txt("")]
        for _, old, new in chunk:
            lines.append((new, "<b>%s</b>" % esc(new)))
            lines.append((_t("was: ") + short(old, 40), _t("<i>was: %s</i>") % esc(short(old, 40))))
        rows = [[{"text": _t("✅ Apply all"), "callback_data": "nm:all"}], auto_btn]
        if pages > 1:
            nav = []
            if page > 0:
                nav.append({"text": _t("◀ Back"), "callback_data": "nm:%d" % (page - 1)})
            nav.append({"text": "%d / %d" % (page + 1, pages), "callback_data": "nm:%d" % page})
            if page < pages - 1:
                nav.append({"text": _t("More ▶"), "callback_data": "nm:%d" % (page + 1)})
            rows.append(nav)
        rows.append(back)
        return build(lines, rows)

    def name_candidates(self, sessions):
        names = self.store.data.get("names", {})
        return [x for x in sessions if x["id"] not in names and x.get("source") != "herdr"
                and (x.get("name_source") == "auto" or not x.get("real_name") or x.get("real_name") == x["id"]
                     or looks_like_prompt(x.get("real_name") or x["name"], x.get("task") or ""))]

    def suggest_names(self, sessions):
        """Batch name generation via claude -p (haiku). Errors -> {}."""
        if not sessions:
            return {}
        payload = [{"id": x["id"], "name": x.get("real_name") or x["name"], "cwd": x["cwd"],
                    "task": short(x.get("task") or "", 300), "status": short(x.get("detail") or "", 160)}
                   for x in sessions]
        rc, out = self.run([self.cfg.claude_bin, "-p", "--model", "haiku", "--output-format", "text"],
                           timeout=180, input_text=name_prompt() + "\n" + json.dumps(payload, ensure_ascii=False))
        if rc != 0:
            log("suggest_names failed: rc=%s" % rc)
            return {}
        return parse_names(out, {x["id"] for x in sessions})

    def start_generation(self, force=False):
        if self.names_busy or self.claude_missing():
            return False
        now = self.clock()
        if not force and now - self.store.data.get("names_last_gen", 0) < NAMES_GEN_INTERVAL:
            return False
        sessions = self.list_sessions(True)
        if sessions is None:
            return False
        batch = self.name_candidates(sessions)[:NAMES_BATCH]
        if not batch:
            return False
        self.names_busy = True
        self.store.data["names_last_gen"] = now
        self.spawn(self._generate_job, batch)
        return True

    def _generate_job(self, batch):
        try:
            sug = self.suggest_names(batch)
        except Exception as e:
            log("suggest_names error: %s" % type(e).__name__)
            sug = {}
        finally:
            self.names_busy = False
        names = self.store.data.setdefault("names", {})
        if sug:
            auto = bool(self.store.data.get("auto_names"))
            for x in batch:
                name = sug.get(x["id"], "")
                skip = (not name) or is_no_task(name)
                names[x["id"]] = {"name": "" if skip else name, "applied": False, "alias": False,
                                  "source_name": x.get("real_name") or x["name"], "skip": skip,
                                  "approved": bool(auto and not skip), "tries": 0}
        self.store.save()
        for uid in self.cfg.allowed:
            if self.panel_state(uid)["mode"] == "names":
                try:
                    self.panel_publish(uid)
                except TGError as e:
                    log("panel publish failed: %s" % e)

    def rename_live(self, sid, rec):
        """/rename into a live session without running the model. Does not touch a busy input field or a menu."""
        name = rec.get("target") or rec["name"]
        with self.tmux.lock(sid):
            ok, _ = self.tmux.ensure(sid)
            if not ok:
                rec["tries"] = rec.get("tries", 0) + 1
                return "failed"
            pane = self.tmux.capture(sid, ansi=True)
            if menu_active(pane) or input_text(pane):
                return "busy"
        ok, err = self.injector(sid, "/rename " + name)
        if not ok:
            if err == ERR_MENU:
                return "busy"
            rec["tries"] = rec.get("tries", 0) + 1
            return "failed"
        self.sleep(1.5)
        fresh = {x["id"]: x for x in (self.list_sessions(True) or [])}.get(sid)
        if fresh and (fresh.get("real_name") or "").strip().lower() == name.strip().lower():
            rec["applied"], rec["alias"], rec["applied_as"] = True, False, name
            return "applied"
        rec["tries"] = rec.get("tries", 0) + 1
        return "failed"

    def _apply_one(self, sid, rec, s, allow_rename=True):
        """-> applied | alias | pending | busy | failed. A stopped session is never brought up."""
        rec["alias"] = True  # until applied — show the new name from the bot
        if is_sdk(sid):  # no /rename and no claude: the name stays a bot alias for good
            return "alias"
        if s is None:
            return "pending"
        if not s.get("pid"):
            # stopped: the name is written to its log and state.json, the session is not brought up (checked: the name is
            # visible in claude agents immediately and is kept after the session comes alive)
            return self.rename_offline(sid, rec, s)
        if not allow_rename:
            return "pending"
        # /rename works while the session is running too (a local command, it does not interrupt the model turn)
        return self.rename_live(sid, rec)

    def rename_offline(self, sid, rec, s):
        name = rec.get("target") or rec["name"]
        path = s.get("link") or fallback_transcript_path(s.get("cwd") or "", s.get("sessionId") or "")
        st_path = os.path.join(self.cfg.jobs_dir, sid, "state.json")
        session_uuid = s.get("sessionId") or ""
        try:
            if not os.path.isfile(st_path):
                raise OSError("no state.json")
            # the log may be deleted for old sessions — then there is nothing to resume, state.json is enough
            if path and session_uuid and os.path.isfile(path):
                with open(path, "rb") as f:
                    f.seek(max(0, os.path.getsize(path) - 1))
                    tail = f.read(1)
                recs = [{"type": "custom-title", "customTitle": name, "sessionId": session_uuid},
                        {"type": "agent-name", "agentName": name, "sessionId": session_uuid}]
                with open(path, "a", encoding="utf-8") as f:
                    if tail and tail != b"\n":
                        f.write("\n")
                    for o in recs:
                        f.write(json.dumps(o, ensure_ascii=False, separators=(",", ":")) + "\n")
            with open(st_path, encoding="utf-8") as f:
                st = json.load(f)
            st["name"], st["nameSource"] = name, "user"
            mode = os.stat(st_path).st_mode & 0o777
            tmp = st_path + ".cctg.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, mode)
            os.replace(tmp, st_path)
        except (OSError, ValueError) as e:
            log("rename offline %s failed: %s" % (sid, type(e).__name__))
            rec["tries"] = rec.get("tries", 0) + 1
            return "failed"
        rec["applied"], rec["alias"], rec["applied_as"] = True, False, name
        return "applied"

    def apply_name(self, sid, name, manual=False):
        name = clean_name(name)
        if not name:
            return "empty"
        names = self.store.data.setdefault("names", {})
        rec = names.setdefault(sid, {})
        if rec.get("manual") and not manual:
            return "manual"
        rec.update({"name": name, "approved": True, "applied": False, "skip": False, "tries": 0,
                    "manual": bool(manual or rec.get("manual"))})
        rec.setdefault("source_name", self.session_info(sid).get("real_name") or sid)
        s = {x["id"]: x for x in (self.list_sessions(True) or [])}.get(sid)
        status = self._apply_one(sid, rec, s)
        self.store.save()
        return status

    def names_tick(self):
        sessions = self.list_sessions(True)
        if sessions is None:
            return
        self.start_generation()
        by_id = {x["id"]: x for x in sessions}
        renamed = False
        for sid, rec in list(self.store.data.get("names", {}).items()):
            if not rec.get("approved") or rec.get("skip") or not rec.get("name") or rec.get("tries", 0) >= 3:
                continue
            target = short_keep_tag(self.chain_label(sid, rec["name"]), 40)
            if rec.get("applied") and rec.get("applied_as", rec["name"]) == target:
                continue
            if rec.get("target") != target:
                rec["target"], rec["applied"], rec["tries"] = target, False, 0
            s = by_id.get(sid)
            status = self._apply_one(sid, rec, s, allow_rename=not renamed)
            if s is not None and s.get("pid") and status in ("applied", "failed"):
                renamed = True  # no more than one /rename of a live session per tick
        self.store.save()

    def name_new_session(self, sid, cwd, text):
        sug = self.suggest_names([{"id": sid, "name": sid, "real_name": sid, "cwd": cwd,
                                   "task": text, "detail": ""}])
        name = sug.get(sid)
        if name and not is_no_task(name):
            self.apply_name(sid, name)

    def panel_tick(self):
        now = self.clock()
        for uid in self.cfg.allowed:
            p = self.panel_state(uid)
            if p["mode"] in ("quota", "help", "clean", "names", "arch", "engine") and now - p["mode_at"] > PANEL_MODE_TTL:
                self.panel_mode(uid, "list")
            if p.get("flash") and p["flash"]["until"] <= now:
                p["flash"] = None
            self.panel_publish(uid)

    def cmd_panel(self, chat, start=False):
        self.panel_mode(chat, "list")
        self.panel_publish(chat, force_new=True)
        if start or self.topics_enabled:
            self.say_temp(chat, _t("The panel is in the topic «%s».") % panel_title() if self.topics_enabled
                          else _t("Keyboard removed. The panel is the message above."),
                          REMOVE_KB if start else None)

    def remove_old_tracker(self):
        for uid in self.cfg.allowed:
            tr = self.store.data.get("tracker", {}).pop(str(uid), None)
            if tr and tr.get("mid"):
                self.tg.delete(uid, tr["mid"])
        for k in [k for k in self.store.data.get("singles", {}) if k.endswith(":list")]:
            mid = self.store.data["singles"].pop(k)
            self.tg.delete(int(k.split(":")[0]), mid)
        self.store.save()

    def panel_new_task(self, chat, text):
        cwd, task = parse_task_text(text, self.cfg.default_cwd)
        if not task:
            self.panel_flash(chat, _t("After the path, write what needs to be done."))
            return
        c = self.store.chat(chat)
        c["pending_task"] = {"cwd": cwd, "text": task}
        eng = self.engine_armed(c)
        c["new_engine"] = None
        if eng:
            self.store.save()
            self.panel_launch(chat, sdk=eng == "sdk")  # the engine was chosen: no second step
            return
        self.panel_mode(chat, "new")
        self.store.save()
        self.panel_publish(chat)

    def panel_launch(self, chat, cwd=None, sdk=False):
        c = self.store.chat(chat)
        pt = c.get("pending_task")
        if not pt:
            self.panel_flash(chat, _t("This task was already started or cancelled."))
            return
        c["pending_task"] = None
        self.panel_mode(chat, "list")
        self.store.save()
        self.panel_flash(chat, _t("Starting in %s…") % tilde(cwd or pt["cwd"]), ttl=30)
        self.spawn(self.do_new, chat, cwd or pt["cwd"], pt["text"], *((True,) if sdk else ()))

    def clean_done_topics(self, chat):
        sids = self.done_topics(chat)
        for sid in sids:
            self.drop_topic(chat, sid)
        self.panel_flash(chat, _t("Topics removed: %d.") % len(sids))

    def send_quota(self, chat):
        if self.claude_missing():
            self.say_temp(chat, _t("The subscription quota is not available without Claude Code."))
            return
        self.panel_mode(chat, "quota")
        self.panel_publish(chat)

    def clean_chat(self, chat):
        now = self.clock()
        keep = {(self.panel_state(chat).get("mid")),
                (self.store.data.get("tracker", {}).get(str(chat)) or {}).get("mid"),
                self.store.chat(chat).get("greet_mid")}
        entries = [e for e in self.store.data.get("msglog", [])
                   if e[0] == chat and now - e[2] <= LOG_TTL and e[1] not in keep]
        ids = [e[1] for e in entries]
        for i in range(0, len(ids), 100):
            self.tg.delete_messages(chat, ids[i:i + 100])
        gone = set(ids)
        self.store.data["msglog"] = [e for e in self.store.data.get("msglog", [])
                                     if not (e[0] == chat and e[1] in gone)]
        for k in [k for k in self.store.data.get("singles", {}) if k.startswith("%s:" % chat)]:
            self.store.data["singles"].pop(k, None)
        with self._temp_lock:
            self.temp = [t for t in self.temp if t[1] != chat]
        self.store.save()
        self.say_temp(chat, _t("Clean ✨"))

    def detect_topics(self):
        try:
            me = self.tg.get_me() or {}
            self.topics_enabled = bool(me.get("has_topics_enabled"))
            if self.tg_chat is self.tg:
                self.chat_username = me.get("username")
        except TGError as e:
            log("getMe failed: %s" % e)
        if self.tg_chat is self.tg:
            self.responder_replay()
        return self.topics_enabled

    def help_text(self):
        if self.claude_missing():
            return help_sdk()
        return help_topics() if self.topics_enabled else help_default()

    # ---- herdr agents (second backend)
    @property
    def herdr(self):
        return getattr(self.tmux, "herdr", None)

    @herdr.setter
    def herdr(self, client):
        self.tmux.herdr = client

    def herdr_quiet(self, e):
        """A broken herdr connection is logged at most once per HERDR_QUIET."""
        now = self.clock()
        if now - self._hd_logged >= HERDR_QUIET:
            self._hd_logged = now
            log("herdr: %s" % e.code)

    def herdr_sessions(self):
        """Agents of herdr as session dicts (same shape as merge_sessions + kind/source). [] when herdr is off or down."""
        h = self.herdr
        if h is None or not h.enabled():
            return []
        try:
            agents = h.agents()
        except HerdrError as e:
            self.herdr_quiet(e)
            return []
        now = self.clock()
        res = []
        for a in agents:
            pane = str(a["pane_id"])
            sid = herdr_sid(pane)
            if not HERDR_ID_RE.match(sid):
                continue
            kind = str(a.get("agent") or a.get("display_agent") or "agent")
            cwd = str(a.get("cwd") or a.get("foreground_cwd") or "")
            name = str(a.get("name") or "").strip() or "%s: %s" % (
                kind.capitalize(), os.path.basename(cwd.rstrip("/")) or cwd or pane)
            rev = a.get("revision")
            seen = self._hd_seen.get(sid)
            if seen is None:
                seen = (rev, now, now)
            elif seen[0] != rev:
                seen = (rev, seen[1], now)
            self._hd_seen[sid] = seen
            cached = self._hd_cache.get(sid)
            if cached is None or (cached[0] != rev and now - cached[1] >= HERDR_DETAIL_EVERY):
                try:
                    lines = [l.strip() for l in strip_ansi(h.read(pane, "recent", 20)).splitlines() if l.strip()]
                    cached = (rev, now, short(lines[-1], 120) if lines else "")
                except HerdrError as e:
                    self.herdr_quiet(e)
                    cached = cached or (rev, now, "")
                self._hd_cache[sid] = cached
            res.append({
                "id": sid, "name": name, "cwd": cwd,
                "state": HERDR_STATES.get(a.get("agent_status"), "working"),
                "detail": cached[2], "result": None, "sessionId": "", "link": "",
                "updated": seen[2], "created": seen[1], "pid": None, "busy": None,
                "name_source": "herdr", "task": "", "kind": kind, "source": "herdr",
                "pane": pane, "workspace": a.get("workspace_id") or ""})
        return res

    def herdr_screen(self, sid, lines=None):
        """-> (ok, text or error) of the visible screen of a herdr pane."""
        h = self.herdr
        if h is None:
            return False, _t("herdr is not responding.")
        try:
            text = h.read(herdr_pane(sid), "visible", lines)
        except HerdrError as e:
            return False, herdr_err_text(e)
        return True, text

    # ---- Claude Code may be absent (SDK-only mode)
    def claude_missing(self):
        """True when there is no claude binary (or CC_CLAUDE=off): nothing is run, only SDK and herdr sessions are shown."""
        if self.cfg.claude_mode == "off":
            return True
        now = self.clock()
        t, v = self._claude_chk
        if t is not None and now - t < (CLAUDE_GONE_TTL if v else CLAUDE_CHECK_TTL):
            return v
        v = shutil.which(self.cfg.claude_bin) is None  # an absolute path is checked for X_OK
        self._claude_chk = (now, v)
        return v

    def _claude_guard(self, runner):
        """The runner that never starts a missing claude: (127, ...) and one log line per CLAUDE_QUIET."""
        def run(args, *a, **kw):
            if args and args[0] == self.cfg.claude_bin and self.claude_missing():
                now = self.clock()
                if self._claude_logged is None or now - self._claude_logged >= SDK_QUIET:
                    self._claude_logged = now
                    log("claude: not installed (SDK-only mode)")
                return 127, "claude is not installed"
            return runner(args, *a, **kw)
        return run

    # ---- SDK sessions (third kind)
    @property
    def sdk(self):
        return getattr(self.tmux, "sdk", None)

    @sdk.setter
    def sdk(self, client):
        self.tmux.sdk = client
        self._sdk_ready = (0.0, False)

    def sdk_on(self):
        c = self.sdk
        return c is not None and c.enabled()

    def sdk_quiet(self, e):
        """A broken runner connection is logged at most once per SDK_QUIET."""
        now = self.clock()
        if now - self._sdk_logged >= SDK_QUIET:
            self._sdk_logged = now
            log("sdk: %s" % e.code)

    def sdk_apply(self, x):
        """Stores a runner session object in `known`. -> (new dict, previous dict)."""
        d = sdk_session_dict(x)
        d["real_name"] = d["base_name"] = d["name"]
        self.sdk_alias(d)
        old = self.known.get(d["id"]) or {}
        self.known[d["id"]] = d
        return d, old

    def sdk_alias(self, d):
        """A bot alias (from the names page) replaces the runner's name; the real name stays in real_name."""
        alias = self.alias_for(d["id"])
        if alias:
            d["name"] = d["base_name"] = alias
        return d

    def sdk_sessions(self):
        """SDK sessions as session dicts. [] when the runner is off or down."""
        if not self.sdk_on():
            return []
        try:
            items = self.sdk.sessions()
        except SdkError as e:
            self.sdk_quiet(e)
            return []
        return [sdk_session_dict(x) for x in items]

    def sdk_ready(self, fresh=False):
        """The runner answers and has an API key (the "via SDK" button is shown only then)."""
        now = self.clock()
        if not fresh and now - self._sdk_ready[0] < SDK_READY_TTL:
            return self._sdk_ready[1]
        ok = False
        if self.sdk_on():
            try:
                ok = self.sdk.call("runner.info").get("key") is True
            except SdkError as e:
                self.sdk_quiet(e)
        self._sdk_ready = (now, ok)
        return ok

    def sdk_stop(self, sid):
        try:
            self.sdk.call("session.stop", {"id": sid}, timeout=10.0)
        except SdkError:
            return False
        return True

    def sdk_text(self, sid, text):
        """session.send as an action of a button. -> error text or None."""
        return self.tmux.sdk_send(sid, text)[1] or None

    def stop_session(self, sid):
        """Stops a session of any kind. -> True when it was stopped."""
        if is_sdk(sid):
            return self.sdk_stop(sid)
        rc, _ = self.run([self.cfg.claude_bin, "stop", sid], timeout=30)
        return rc == 0

    def card_mk(self, sid, current=False, watched=False, topic=False, old=False, archived=False):
        extra = sdk_action_rows(self.known.get(sid) or {"id": sid}) if is_sdk(sid) else None
        return card_markup(sid, current, watched, topic, old, archived, extra)

    def sdk_button(self, chat, mid, kind, sid):
        """sdb: +1 $ and continue; sdc: continue; sdi: interrupt."""
        try:
            s = self.sdk.get(sid)
        except SdkError as e:
            self.popup(chat, "⚠️ " + sdk_err_text(e), sid=sid, alert=True)
            return
        d, _ = self.sdk_apply(s) if s else (None, None)
        if (d or {}).get("sdk_state") not in SDK_BUTTON_STATES[kind]:
            self.popup(chat, _t("This button is outdated."), sid=sid)
            return
        err = None
        if kind == "sdi":
            try:
                self.sdk.call("session.interrupt", {"id": sid}, timeout=10.0)
            except SdkError as e:
                err = sdk_err_text(e)
            note = _t("⏸ Interrupted.")
        else:
            if kind == "sdb":
                cur = (d or {}).get("budget_usd")
                try:
                    self.sdk.call("session.budget", {"id": sid, "budget_usd": float(cur or 0) + 1.0}, timeout=10.0)
                except SdkError as e:
                    err = sdk_err_text(e)
            err = err or self.sdk_text(sid, "continue")
            note = _t("⟳ Continuing.")
            if not err:
                c = self.store.chat(chat)
                self.start_turn(c, sid)
                self.store.save()
        self.popup(chat, "⚠️ " + err if err else note, sid=sid, alert=bool(err))

    # ---- permission requests of SDK sessions
    def sdk_perms(self):
        return self.store.data.setdefault("sdk_perms", {})

    def sdk_perm_head(self, rec):
        return _t("🔐 %s: needs your permission — %s") % (rec["name"], rec["tool"])

    def sdk_perm_markup(self, chat, rec):
        def b(text, action):
            return {"text": text, "callback_data": "pr:" + self.qkey(chat, rec["sid"], rec["req"], action)}
        rows = [[b(_t("✅ Allow"), "allow")]]
        if rec.get("can_always"):
            rows.append([b(_t("♾ Always %s in this session") % short(rec["tool"], 30), "always")])
        rows.append([b(_t("✖ Deny"), "deny")])
        return {"inline_keyboard": rows}

    def sdk_show_perm(self, sid, req, skip=()):
        """A permission request goes to the chats that track the session; the store keeps what was shown."""
        if not isinstance(req, dict) or req.get("kind") != "perm" or not req.get("req"):
            return
        if self.is_old(sid) or self.is_archived(sid):
            return
        perms = self.sdk_perms()
        for chat in sorted(self.cfg.allowed):
            key = "%s:%s" % (chat, req["req"])
            if chat in skip or key in perms:
                continue  # a permission request blocks the work: shown whether or not the session is subscribed to
            rec = {"chat": chat, "sid": sid, "req": str(req["req"]), "tool": str(req.get("tool") or "?"),
                   "desc": sdk_input_text(req.get("tool"), req.get("input")), "can_always": bool(req.get("can_always")),
                   "name": short(self.session_name(sid), 60), "mid": None, "closed": False}
            html = "<b>%s</b>\n%s" % (esc(self.sdk_perm_head(rec)), sdk_perm_html(req.get("tool"), req.get("input")))
            plain = self.sdk_perm_head(rec) + "\n" + rec["desc"]
            if self.topics_enabled:
                self.ensure_topic(chat, sid, perm_only=not self.tracked_c(self.store.chat(chat), sid))
            self.touch_topic(chat, sid)
            markup = self.sdk_perm_markup(chat, rec)
            r = self._threaded(chat, sid, lambda th: self.tg.send_rich(chat, html, plain, markup, thread=th))
            rec["mid"] = r.get("message_id") if isinstance(r, dict) else None
            perms[key] = rec
            while len(perms) > SDK_PERM_KEEP:  # closed records go first, open ones only if there is no other way
                del perms[next((k for k, r in perms.items() if r.get("closed")), next(iter(perms)))]
            self.remember(chat, [r], sid)

    def sdk_show_pending(self, sid):
        """Requests that came before the session was tracked are shown now (nothing is shown twice)."""
        try:
            for r in self.sdk.get(sid).get("pending") or []:
                self.sdk_show_perm(sid, r)
        except (SdkError, AttributeError) as e:
            if isinstance(e, SdkError):
                self.sdk_quiet(e)
        except TGError as e:
            log("perm show failed: %s" % e)

    def sdk_end_perm(self, rec, note):
        rec["closed"] = True
        if rec.get("mid"):
            try:
                what = short(" ".join(str(rec.get("desc") or "").split()), 80)
                self.tg.edit(rec["chat"], rec["mid"], " · ".join(x for x in ("🔐 " + rec["tool"], what, note) if x))
            except TGError as e:
                log("perm close failed: %s" % e)
        self.store.save()

    @staticmethod
    def sdk_decision_note(decision, tool="?"):
        if decision == "always":
            return _t("♾ %s allowed for the session") % tool
        return {"allow": _t("✔ Allowed"), "deny": _t("✖ Denied")}.get(decision, _t("Already decided"))

    def sdk_perm_press(self, chat, mid, n):
        ent = self.store.data.get("qmap", {}).get(str(n))
        if not ent:
            self.popup(chat, _t("This button is outdated."))
            return
        _, sid, req, action = ent
        rec = self.sdk_perms().get("%s:%s" % (chat, req))
        if not rec or action not in ("allow", "always", "deny"):
            self.popup(chat, _t("This button is outdated."))
            return
        with self._sdk_lock:
            if rec.get("closed") or rec.get("deciding"):
                busy = True
            else:
                busy = False
                rec["deciding"] = True
        if busy:
            if rec.get("closed"):
                if mid:
                    self.sdk_end_perm(rec, _t("Already decided"))
                self.popup(chat, _t("Already decided"))
            else:
                self.popup(chat, _t("Processing…"))
            return
        try:
            res = self.sdk.call("session.answer", {"id": sid, "req": req, "decision": action}, timeout=10.0)
        except SdkError as e:
            rec.pop("deciding", None)
            self.popup(chat, "⚠️ " + sdk_err_text(e), sid=sid, alert=True)
            return
        self.sdk_end_perm(rec, _t("Already decided") if res.get("already") else self.sdk_decision_note(action, rec["tool"]))
        rec.pop("deciding", None)

    def sdk_close_perm(self, sid, req, decision):
        """request.closed: the request was resolved elsewhere (another device, interrupt, restart)."""
        for rec in list(self.sdk_perms().values()):
            if rec["req"] == req and rec["sid"] == sid and not rec.get("closed") and not rec.get("deciding"):
                self.sdk_end_perm(rec, self.sdk_decision_note(decision, rec["tool"]))

    def sdk_turn_result(self, data):
        """Only remembers the cost: it is shown in the card and in the «done» notification, not as a message."""
        sid = str(data.get("id") or "")
        k = self.known.get(sid)
        cost = data.get("cost_usd")
        if k is not None and isinstance(cost, (int, float)):
            k["cost_usd"] = cost
            for uid in sorted(self.cfg.allowed):
                self.refresh_card(uid, sid)

    def sdk_reconcile(self):
        """After (re)subscribing: show unanswered requests that appeared meanwhile, close the ones that are gone."""
        items = self.sdk.sessions()
        pending = {}
        for x in items:
            self.sdk_apply(x)
            for r in x.get("pending") or []:
                if isinstance(r, dict) and r.get("req"):
                    pending[(x["id"], str(r["req"]))] = r
        for rec in list(self.sdk_perms().values()):
            if not rec.get("closed") and (rec["sid"], rec["req"]) not in pending:
                self.sdk_end_perm(rec, _t("Already decided"))
        for (sid, _), r in pending.items():
            try:
                self.sdk_show_perm(sid, r)
            except TGError as e:
                log("perm show failed: %s" % e)

    # ---- chat responder: questions from one group chat, answered by a read-only SDK session

    def chat_name(self):
        return self.cfg.responder_name or self.chat_username or "Bot"

    def responder_on_message(self, m, edited=False):
        text, me = m.get("text"), self.chat_username or self.chat_me()
        if not isinstance(text, str) or self.store.data.get("responder", {}).get("off"):
            return
        if not me:  # the bot's own name is unknown (no network at start): keep it, answered once the name is known
            missed = self.store.data.setdefault("resp_missed", [])
            missed[:] = (missed + [m])[-30:]
            self.store.save()
            log("responder: name unknown, message %s kept" % m.get("message_id"))
            return
        handled = self.store.data.setdefault("resp_mids", [])
        if edited and m.get("message_id") in handled:
            return
        mention = re.compile(r"@%s(?!\w)" % re.escape(me), re.I)  # also "/cmd@bot"
        to_bot = bool(mention.search(text)) or str(
            ((m.get("reply_to_message") or {}).get("from") or {}).get("username") or "").lower() == me.lower()
        q = mention.sub("", text).strip(" ,:\n")
        if _REPORT_WORD.fullmatch(q) and self.cfg.role != "responder" and self.responder_git():  # «отчёт», with or without the mention: the last day of the project
            return self.report_request(m)
        if not to_bot:
            return
        if re.fullmatch(r"/start(\s.*)?", q, re.S):  # Telegram posts /start@bot itself when the bot is added to a group
            return
        if not q:  # a bare mention: about the message it replies to
            log("responder bare mention: %s" % ",".join(sorted(m)))
            if not self.reply_context(m):
                # Telegram leaves the reply out when the bot cannot see that message (sent before it joined)
                try:
                    self.tg_chat.send(self.cfg.responder_chat, _t(
                        "I only see the mention. If it is a reply, that message is hidden from me (bots do not see "
                        "messages sent before they joined the chat): forward it or write the question."),
                        reply_to=m.get("message_id"))
                except TGError as e:
                    log("responder bare mention reply failed: %s" % e)
                return
            q = _t("What do you think about this?")
        sid = self.store.data.get("responder", {}).get("sid")
        waits = (self.known.get(sid) or {}).get("sdk_state") == "blocked"  # the owner has not decided yet
        with self._resp_lock:
            stuck = self._resp_busy and not waits and self.clock() - self._resp_busy["ts"] > RESPONDER_TIMEOUT
        if stuck:
            self.responder_drop()
        who = (m.get("from") or {}).get("first_name") or "?"
        now = self.clock()
        with self._resp_lock:
            self._resp_times = [t for t in self._resp_times if now - t < 3600]
            if len(self._resp_times) >= self.cfg.responder_rate:
                log("responder: hourly limit")
                return
            self._resp_times.append(now)
            self._resp_queue.append({"mid": m.get("message_id"), "q": q, "who": who, "ctx": self.reply_context(m),
                                     "re_bot": self.reply_to_bot(m),
                                     "from": (m.get("from") or {}).get("id")})
            handled[:] = (handled + [m.get("message_id")])[-200:]
        try:  # seen at once: the answer may take a while
            self.tg_chat.call("setMessageReaction", {"chat_id": self.cfg.responder_chat, "message_id": m.get("message_id"),
                                                     "reaction": [{"type": "emoji", "emoji": "👀"}]})
        except TGError as e:
            log("responder reaction failed: %s" % e)
        self.responder_next()

    def relay_capture(self, m):
        """A message sent to the chat on the owner's behalf (relay.json next to the responder log: its message id and
        a deadline) waits for answers: until then participants' messages are logged to relay.jsonl, and a reply
        to that message is not answered by the responder. -> True when the message was such a reply."""
        d = os.path.dirname(self.cfg.responder_log)
        try:
            with open(os.path.join(d, "relay.json"), encoding="utf-8") as f:
                relay = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(relay, dict) or self.clock() > relay.get("until", 0) or (m.get("from") or {}).get("id") in self.cfg.allowed:
            return False
        text = m.get("text") or m.get("caption") or ""
        reply = (m.get("reply_to_message") or {}).get("message_id") in (relay.get("mids") or [relay.get("mid")])
        try:
            with open(os.path.join(d, "relay.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": self.clock(), "mid": m.get("message_id"), "who": (m.get("from") or {}).get("first_name"),
                                    "reply": reply, "text": text}, ensure_ascii=False) + "\n")
        except OSError as e:
            log("relay log failed: %s" % type(e).__name__)
        # a question in the reply (a mention of the bot or «?») is still the bot's: such questions used to get lost
        asks = "?" in text or bool(self.chat_username and re.search(r"@%s(?!\w)" % re.escape(self.chat_username), text, re.I))
        return reply and not asks

    def chat_tail_add(self, who, text, mid=None):
        """The last messages of the shared chat (the bot's answers too): every question carries them, so neither a
        fresh session nor the owner's and the guests' separate sessions lose the thread."""
        if "chat_tail" not in self.store.data:  # first run: the thread so far, from the responder log
            seed = []
            try:
                with open(self.cfg.responder_log, encoding="utf-8") as f:
                    for ln in f.readlines()[-CHAT_TAIL // 2:]:
                        d = json.loads(ln)
                        seed += [{"who": d.get("who") or "?", "text": short(str(d.get("q") or ""), 600), "mid": None},
                                 {"who": self.chat_name(), "text": short(str(d.get("a") or ""), 600), "mid": None}]
            except (OSError, ValueError):
                pass
            self.store.data["chat_tail"] = seed
        tail = self.store.data["chat_tail"]
        tail.append({"who": who, "text": short(str(text), 600), "mid": mid})
        del tail[:-CHAT_TAIL]
        self.store.save()

    def reply_to_bot(self, m):
        """The bot's own message a question replies to (not «someone's words»: the owner's reply stays his own)."""
        r = m.get("reply_to_message") or {}
        if str((r.get("from") or {}).get("username") or "").lower() == str(self.chat_username or "").lower():
            return short(str(r.get("text") or r.get("caption") or ""), 1500) or None
        return None

    def reply_context(self, m):
        """The message a question replies to (a forward, someone's words), unless it is the bot's own."""
        r = m.get("reply_to_message") or {}
        x = m.get("external_reply") or {}  # «reply in another chat»: no reply_to_message, the text only in quote
        o = x.get("origin") or {}
        text = (m.get("quote") or {}).get("text") or r.get("text") or r.get("caption") or (
            (_t("[a message without text]") if r or x else None))
        if not isinstance(text, str) or str((r.get("from") or {}).get("username") or "").lower() == str(
                self.chat_username or "").lower():
            return None
        who = ((r.get("from") or o.get("sender_user") or {}).get("first_name") or o.get("sender_user_name")
               or (o.get("sender_chat") or o.get("chat") or {}).get("title") or "?")
        return (who, short(text, 1500))

    def backlog(self):
        return self.store.data.setdefault("backlog", [])

    def backlog_add(self, rec):
        d = self.store.data
        d["bl_seq"] = d.get("bl_seq", 0) + 1
        rec["bl"] = "b%d" % d["bl_seq"]
        bl = self.backlog()
        bl.append({"id": rec["bl"], "who": rec["who"], "from": rec.get("from"), "q": rec["q"], "ctx": rec.get("ctx"),
                   "mid": rec.get("mid"), "ts": self.clock(), "status": "waiting"})
        del bl[:-50]
        self.store.save()

    def backlog_item(self, bid):
        return next((x for x in self.backlog() if x["id"] == bid), None)

    def backlog_set(self, bid, status):
        it = self.backlog_item(bid)
        if it is not None:
            it["status"] = status
            self.store.save()
        return it

    @staticmethod
    def backlog_text(it):
        """Exactly what ▶️ runs as the owner's own request: the card shows all of it, nothing is cut."""
        ctx = _t(" (in reply to %s: «%s»)") % tuple(it["ctx"]) if it.get("ctx") else ""
        return _t("%s asked: «%s»%s. The owner confirmed it: do it now.") % (it["who"], it["q"], ctx)

    def backlog_show(self, it, chats=None):
        """The owner gets the request with buttons: do it now (as the owner's own request) or drop it."""
        mk = {"inline_keyboard": [[{"text": _t("▶️ Do it"), "callback_data": "bl:%s:run" % it["id"]},
                                   {"text": _t("✖ Remove"), "callback_data": "bl:%s:drop" % it["id"]}]]}
        for uid in chats or sorted(self.cfg.allowed):
            try:
                self.tg.send(uid, "📋 " + self.backlog_text(it), mk)  # split into parts if long, buttons on the last
            except TGError as e:
                log("backlog notice failed: %s" % e)

    def backlog_press(self, chat, bid, action):
        it = self.backlog_item(bid)
        if it is None or it["status"] not in ("open", "waiting"):
            self.popup(chat, _t("Already decided"))
            return
        if action == "drop":
            self.backlog_set(bid, "dropped")
            self.popup(chat, _t("Removed from the backlog."))
            return
        self.backlog_set(bid, "queued")
        with self._resp_lock:  # the owner's own request now: it runs without one more confirmation
            self._resp_queue.append({"mid": it.get("mid"), "who": it["who"], "ctx": None, "from": chat, "bl": bid,
                                     "q": self.backlog_text(it)})
        self.popup(chat, _t("Queued for the chat."))
        self.responder_next()

    def backlog_on_start(self):
        """Requests that waited for the owner when the bot stopped are open again, with their buttons."""
        st = self.store.data.get("responder") or {}
        if any(it["status"] in ("waiting", "queued") for it in self.backlog()):
            for k in ("sid_owner", "sid_guest"):  # the runner may still hold the 🔐: no second way to run it
                self.responder_deny_pending(st.get(k))
        for it in self.backlog():
            if it["status"] in ("waiting", "queued"):
                it["status"] = "open"
                self.backlog_show(it)
        self.store.save()

    def responder_deny_pending(self, sid):
        """The 🔐 of a parked request is closed: its backlog card is the only way to run it."""
        if not sid:
            return
        try:
            for r in self.sdk.get(sid).get("pending") or []:
                self.sdk.call("session.answer", {"id": sid, "req": str(r.get("req")), "decision": "deny"}, timeout=10.0)
        except (HerdrError, AttributeError) as e:
            log("responder deny failed: %s" % type(e).__name__)

    def responder_park(self):
        """The owner has not decided for RESPONDER_WAIT: the request goes to the backlog and the chat moves on."""
        rec = self._resp_busy
        if not rec or not rec.get("waited_ts") or rec.get("parked") or self.clock() - rec["waited_ts"] < RESPONDER_WAIT:
            return
        rec["parked"] = True
        self.responder_deny_pending((self.store.data.get("responder") or {}).get("sid"))
        it = self.backlog_set(rec.get("bl"), "open")
        try:
            self.tg_chat.send(self.cfg.responder_chat, _t("📋 The owner has not confirmed yet — it is in the backlog, nothing is lost."),
                              reply_to=rec.get("mid"))
        except TGError as e:
            log("responder park notice failed: %s" % e)
        if it:
            self.backlog_show(it)

    def responder_typing_tick(self):
        """«typing…» in the chat while a question is being answered (not while the owner decides a change)."""
        self.responder_park()
        sid = (self.store.data.get("responder") or {}).get("sid")
        if self._resp_busy and self.cfg.responder_chat is not None and (self.known.get(sid) or {}).get("sdk_state") != "blocked":
            self.tg_chat.call("sendChatAction", {"chat_id": self.cfg.responder_chat, "action": "typing"})

    def chat_update(self, upd):
        """A group update for the shared chat. -> True when handled (groups never reach the console)."""
        edited = "edited_message" in upd  # a mention added by editing is a question too, answered once
        m = upd.get("message") or upd.get("edited_message") or {}
        if self.cfg.responder_chat is not None and (m.get("chat") or {}).get("id") == self.cfg.responder_chat:
            if edited:
                self.responder_on_message(m, edited=True)
                return True
            if m.get("text") or m.get("caption"):
                self.chat_tail_add((m.get("from") or {}).get("first_name") or "?", m.get("text") or m.get("caption"), m.get("message_id"))
            if not self.relay_capture(m):
                self.responder_on_message(m)
            return True
        if (m.get("chat") or {}).get("type") in ("group", "supergroup"):
            if not edited:
                self.group_id_command(m)  # any other group: only «@bot id», to set the responder up
            return True
        return edited  # an edited private message is never a console command

    def chat_poll_once(self):
        updates = self.tg_chat.get_updates(self.store.data.get("chat_offset", 0)) or []
        for u in updates:
            try:
                self.chat_update(u)
            except Exception as e:
                log("chat handler error: %s" % type(e).__name__)
            self.store.data["chat_offset"] = u["update_id"] + 1
            self.store.save()

    def responder_replay(self):
        """Messages kept while the bot's name was unknown go through the responder now, oldest first."""
        missed = self.store.data.pop("resp_missed", None)
        if not missed or not self.chat_username:
            if missed:
                self.store.data["resp_missed"] = missed
            return
        self.store.save()
        log("responder: %d kept message(s) answered now" % len(missed))
        for m in missed:
            try:
                self.responder_on_message(m)
            except Exception as e:
                log("responder replay error: %s" % type(e).__name__)

    def chat_me(self):
        """The bot's @name when getMe failed at start (03.11: no network after a reboot left the group deaf for 6 h)."""
        if self.clock() - getattr(self, "_me_tried", -1e9) < 30:
            return None
        self._me_tried = self.clock()
        try:
            self.chat_username = (self.tg_chat.get_me() or {}).get("username")
        except TGError as e:
            log("chat bot getMe failed: %s" % e)
        if self.chat_username and self.store.data.get("resp_missed"):
            threading.Thread(target=self.responder_replay, daemon=True).start()  # not inside the current message
        return self.chat_username

    def chat_poll_loop(self):
        try:
            self.chat_username = (self.tg_chat.get_me() or {}).get("username")
        except TGError as e:
            log("chat bot getMe failed: %s" % e)
        self.responder_replay()
        while not self.stop_event.is_set():
            try:
                self.chat_poll_once()
            except Exception as e:
                log("chat poll error: %s" % type(e).__name__)
                self.stop_event.wait(5)

    def group_id_command(self, m):
        me, text = self.chat_username, m.get("text")
        joined = any(str(u.get("username") or "").lower() == str(me or "").lower()
                     for u in m.get("new_chat_members") or [] if isinstance(u, dict))  # the bot was just added
        if not me or not (joined or isinstance(text, str) and text.strip().lower() in ("@%s id" % me.lower(),
                                                                                       "/id@%s" % me.lower())):
            return
        chat = m["chat"]
        log("group id asked: %s" % chat.get("id"))
        try:
            for uid in sorted(self.cfg.allowed):
                self.tg.send(uid, _t("The bot was asked for the id of the chat «%s»: %s") % (
                    chat.get("title") or "?", chat.get("id")))
        except TGError as e:
            log("group id reply failed: %s" % e)

    def responder_prompt(self, rec, first):
        brief = ""
        if first and self.cfg.responder_brief:
            try:
                with open(self.cfg.responder_brief, encoding="utf-8") as f:
                    brief = f.read().strip() + "\n\n"
            except OSError as e:
                log("responder brief: %s" % type(e).__name__)
        tail = [x for x in self.store.data.get("chat_tail", []) if x.get("mid") is None or x.get("mid") != rec.get("mid")]
        recent = ("Recent messages in the chat, oldest first (your own answers are «%s»):\n%s\n\n" % (self.chat_name(), "\n".join(
            "%s: %s" % (x["who"], " ".join(x["text"].split())) for x in tail))) if tail else ""
        if rec.get("re_bot"):
            recent += "It replies to your earlier answer: «%s»\n\n" % rec["re_bot"]
        act = ("If it asks to change something, say that you can only read and answer, not change anything."
               if self.cfg.responder_engine == "codex" else
               "If it asks to change something, do it: every change, command or file write is sent "
               "to the owner to confirm first, so say what you are about to do.")
        return (brief + recent + "A message from %s in the group chat. Answer it fully: questions about the project and "
                "general ones. %s Answer briefly; do not make "
                "things up.\n\n%s%s" % (rec["who"] + (" (the owner himself: talk to him directly, never «pass it to the "
                                                        "owner»)" if rec.get("from") in self.cfg.allowed else ""), act, "In reply to a message from %s: «%s»\n\n" % rec["ctx"]
                                           if rec.get("ctx") else "", rec["q"]))

    def responder_auto(self, sid, req):
        """The owner's own question runs without confirmation. -> True when the request was allowed here."""
        rec = self._resp_busy
        if self.responder_kind(rec) != "owner":  # someone else's words: ask
            return False  # reading needs no request at all: the chat session's bash runs in a read-only sandbox
        try:
            self.sdk.call("session.answer", {"id": sid, "req": str(req.get("req")), "decision": "allow"}, timeout=10.0)
        except HerdrError as e:
            log("responder auto-allow failed: %s" % e.code)
            return False
        return True

    def responder_waits(self, req):
        """A change asked for in the chat waits for the owner (the 🔐 with buttons goes to the owner only)."""
        rec = self._resp_busy
        if rec is not None:
            if rec.get("waited"):
                return  # one «waiting» per question in the group; every request still reaches the owner as 🔐
            rec["waited"], rec["waited_ts"] = True, self.clock()
            if not rec.get("bl"):  # kept in the store: a restart or a long wait does not lose the request
                self.backlog_add(rec)
        head, body = sdk_input_parts(req.get("tool"), req.get("input"))
        try:
            self.tg_chat.send(self.cfg.responder_chat, _t("⏳ Waiting for the owner to confirm: %s") % perm_brief(
                str(req.get("tool") or "?"), short(body or head, 80)), reply_to=rec["mid"] if rec else None)
            if rec:  # the 🔐 that follows is a chat participant's request, not the owner's own session
                for uid in sorted(self.cfg.allowed):
                    self.tg.send(uid, _t("💬 Asked in the chat by %s: %s") % (rec["who"], short(rec["q"], 500)))
        except TGError as e:
            log("responder wait notice failed: %s" % e)

    def responder_next(self):
        with self._resp_lock:
            if self._resp_busy or not self._resp_queue:
                return
            rec = self._resp_busy = self._resp_queue.pop(0)
            rec["ts"] = self.clock()
        self.responder_pull()
        if self.cfg.responder_engine == "codex":  # stateless: every question carries the brief and the chat tail
            self.responder_codex_run(self.responder_prompt(rec, True) + self.CODEX_RO, "", rec)
            return
        self.responder_sdk(rec)

    def responder_sdk(self, rec):
        """The question goes to the SDK runner's chat session (the console's own model)."""
        st = self.store.data.setdefault("responder", {})
        kind = self.responder_kind(rec)  # the owner's own requests: a session that may write in the project
        st["sid"] = st.get("sid_" + kind)  # «sid» = the session of the question being answered
        if st["sid"] and st.get("n_" + kind, 0) < RESPONDER_TURNS:
            try:
                st["n_" + kind] = st.get("n_" + kind, 0) + 1
                self.sdk.call("session.send", {"id": st["sid"], "text": self.responder_prompt(rec, False),
                                               "model": self.responder_model(rec)}, timeout=10.0)
                return
            except HerdrError as e:  # the session is gone or stuck: one more try with a fresh one
                log("responder send failed: %s" % e.code)
        try:
            x = self.sdk.call("session.start", {"cwd": self.cfg.responder_cwd, "chat": True, "model": self.responder_model(rec),
                                                "write": kind == "owner", "prompt": self.responder_prompt(rec, True)}, timeout=15.0)
            st["sid"] = st["sid_" + kind] = (x.get("session") or {}).get("id")
            st["n_" + kind] = 1
            self.store.save()
        except HerdrError as e:
            log("responder start failed: %s" % e.code)
            rec.setdefault("first_ts", rec["ts"])
            if e.code == "unavailable" and self.clock() - rec["first_ts"] < RESPONDER_RUNNER_WAIT:
                # the runner is still starting (right after an install or a reboot): the question waits for it
                with self._resp_lock:
                    self._resp_busy = None
                    self._resp_queue.insert(0, rec)
                self._spawn(lambda: (self.sleep(3), self.responder_next()))
                return
            self.responder_drop()

    def responder_git(self):
        """A plain folder of documents is a valid responder folder: pull and the «отчёт» report need a git clone."""
        return os.path.exists(os.path.join(self.cfg.responder_cwd, ".git"))

    def responder_pull(self):
        if self.responder_git() and self.clock() - self._resp_pulled > 600:  # answers come from a current clone; local edits are kept (ff only)
            self._resp_pulled = self.clock()
            rc, _ = self.run(["git", "-C", self.cfg.responder_cwd, "pull", "--ff-only", "--quiet"], timeout=30)
            if rc == 0:
                self._resp_pull_told = False
                return
            # the runner drops git's stderr; what blocks a pull is nearly always the clone's own edits
            _, dirty = self.run(["git", "-C", self.cfg.responder_cwd, "status", "--short"], timeout=30)
            why = short(" ".join(str(dirty).split()), 200) or "git pull failed"
            log("responder clone pull failed: %s" % why)
            if not self._resp_pull_told:  # a stuck clone answers from old data, so the owner is told once
                self._resp_pull_told = True
                for uid in sorted(self.cfg.allowed):
                    try:
                        self.tg.send(uid, "⚠️ Клон чата не обновляется, бот отвечает по старым данным: %s" % why)
                    except TGError as e:
                        log("responder owner notice failed: %s" % e)

    def report_dir(self):
        return os.path.join(os.path.dirname(self.cfg.responder_log), "reports")

    def report_build(self):
        """The last day of origin/main in the chat clone -> (token, data); kept as reports/<token>.json."""
        self.responder_pull()
        now = self.clock()
        rc, out = self.run(["git", "-C", self.cfg.responder_cwd, "log", "origin/main", "--no-merges",
                            "--since=@%d" % int(now - 86400), "--format=%x1e%h%x1f%ct%x1f%an%x1f%s", "--name-only"], timeout=30)
        commits = parse_report_log(out) if rc == 0 else []
        data = {"since": now - 86400, "until": now, "commits": commits,
                "counts": {a: sum(c["area"] == a for c in commits) for a in ("site", "data", "server", "other")}}
        token = secrets.token_urlsafe(16)
        d = self.report_dir()
        os.makedirs(d, mode=0o700, exist_ok=True)
        for f in os.listdir(d):  # old links stop working
            try:
                if now - os.path.getmtime(os.path.join(d, f)) > REPORT_KEEP:
                    os.remove(os.path.join(d, f))
            except OSError:
                pass
        with open(os.path.join(d, token + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return token, data

    def report_json(self, token):
        """GET /r/<token>.json: the report data, or None (unknown or expired)."""
        if not _REPORT_TOKEN.fullmatch(token or ""):
            return None
        p = os.path.join(self.report_dir(), token + ".json")
        try:
            if self.clock() - os.path.getmtime(p) > REPORT_KEEP:
                return None
            with open(p, "rb") as f:
                return f.read()
        except OSError:
            return None

    def report_request(self, m):
        """«отчёт» in the chat: the model sums up the day for the chat, the page link (if the page is there) follows."""
        token, data = self.report_build()
        base = self.webapp_url()
        page = os.path.isfile(os.path.join(self.cfg.webapp_static, "report.html"))
        names = {"site": "сайт", "data": "прогнозы и данные", "server": "сервер", "other": "прочее"}
        lines = ["%s: %s" % (names[c["area"]], c["subject"]) for c in data["commits"][:80]]
        q = ("Сводка за последние сутки для чата. Ниже изменения проекта (origin/main), по одному на строку, с "
             "разделом. Напиши до 6 коротких строк по-русски: сначала что нового на сайте для пользователя "
             "(интерфейс, вид, удобство), потом прогнозы и данные, остальное одной строкой. Без хешей, путей и "
             "названий веток. Если изменений нет, так и скажи одной фразой.\n\n" + ("\n".join(lines) or "(нет изменений)"))
        with self._resp_lock:
            self._resp_queue.append({"mid": m.get("message_id"), "q": q, "who": (m.get("from") or {}).get("first_name") or "?",
                                     "ctx": None, "from": None, "model": "",  # a summary: never a change, the default model
                                     "link": base.rstrip("/") + "/r/" + token if base and page else None})
        self.responder_next()

    def responder_kind(self, rec):
        """"owner": the owner's own words; "guest": anyone else's, or the owner replying to someone's message."""
        return "owner" if rec and rec.get("from") in self.cfg.allowed and not rec.get("ctx") else "guest"

    @staticmethod
    def responder_model(rec):
        """"opus" for a change request (in the question or the message it replies to), "" = the runner's default."""
        if "model" in rec:
            return rec["model"]
        return RESPONDER_CHANGE_MODEL if asks_change(rec["q"] + " " + (rec.get("ctx") or ("", ""))[1]) else ""

    def responder_retry(self):
        """The session's budget ran out mid-question: the same question once more in a fresh session
        (the recent chat goes with it). -> True when it was queued again."""
        with self._resp_lock:
            rec = self._resp_busy
            if not rec or rec.get("retried"):
                return False
            rec["retried"] = True
            self._resp_busy = None
            self._resp_queue.insert(0, rec)
        log("responder: budget ran out, the question goes to a fresh session")
        self.responder_next()
        return True

    def responder_forget(self):
        """The current question's session is not used again (the other kind keeps its own)."""
        st = self.store.data.setdefault("responder", {})
        sid = st.pop("sid", None)
        for k in ("sid_owner", "sid_guest"):
            if sid and st.get(k) == sid:
                st.pop(k)
        self.store.save()

    def responder_codex(self, ev):
        """The Claude session stopped on the quota limit or an empty API balance (a fresh session would hit it too): the question goes
        to Codex in a thread. -> True when it was taken (not the limit, switched off, or no question)."""
        if os.environ.get("CC_RESPONDER_CODEX_FALLBACK") == "0" or not self._resp_busy:
            return False
        if not quota_stop(ev):
            return False
        rec = self._resp_busy
        if rec.get("fell"):  # Codex already handed this question over: both are out, no ping-pong
            return False
        rec["fell"] = True
        prompt = self.responder_prompt(rec, True) + self.CODEX_RO
        self.responder_forget()
        log("responder: Claude quota is out, the question goes to Codex")
        self.responder_codex_run(prompt, "\n\n(ответил Codex: квота Claude исчерпана)")
        return True

    CODEX_RO = "\n\nОтвечай по-русски, кратко; ничего не меняй в файлах."

    def responder_codex_run(self, prompt, suffix, rec=None):
        """The question goes to Codex in a thread; any failure is a plain «could not answer» and the queue moves on.
        With `rec` (Codex is the chosen engine) a used-up Codex quota hands the question to the SDK runner once."""
        def work():
            text, out = None, ""
            try:
                r = subprocess.run(codex_boxed(self.cfg.responder_cwd, prompt, self.cfg.codex_model),
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
                out = (r.stdout or "") + (r.stderr or "")
                if r.returncode == 0 and r.stdout.strip():
                    text = r.stdout.strip() + suffix
            except (OSError, subprocess.SubprocessError) as e:
                log("responder codex failed: %s" % type(e).__name__)
            if (text is None and rec is not None and not rec.get("fell") and quota_stop(out) and self.sdk
                    and os.environ.get("CC_RESPONDER_CODEX_FALLBACK") != "0" and self._resp_busy is rec):
                rec["fell"], rec["note"] = True, "\n\n(ответил Claude: квота Codex исчерпана)"
                log("responder: Codex quota is out, the question goes to the SDK runner")
                self.responder_sdk(rec)
                return
            self.responder_answer(text)
        self._spawn(work)

    def responder_drop(self):
        """The current question gets no answer from the session: the next one starts a fresh session."""
        self.responder_forget()
        self.responder_answer(None)

    def responder_files(self, out):
        """Lines «[[файл: docs/x.md]]» in an answer ask the bot to post the file: the chat session has no network.
        Only documents under docs/ of the chat clone: a guest's question must not pull .env or the like. -> text, paths."""
        docs = os.path.realpath(os.path.join(self.cfg.responder_cwd, "docs"))
        files, lines = [], []
        for line in out.split("\n"):
            m = re.fullmatch(r"\s*\[\[файл:\s*(.+?)\s*\]\]\s*", line)
            if not m:
                lines.append(line)
                continue
            path = os.path.realpath(os.path.join(self.cfg.responder_cwd, m.group(1)))
            if (path.startswith(docs + os.sep) and path.lower().endswith((".md", ".pdf", ".txt", ".csv"))
                    and os.path.isfile(path) and os.path.getsize(path) <= 20 * 1024 * 1024):
                files.append(path)
            else:
                lines.append("Файл %s отправить не могу." % os.path.basename(m.group(1)))
        return "\n".join(lines).strip() or "Файл ниже.", files

    def responder_answer(self, text):
        """The responder session finished a turn (text None: it failed)."""
        with self._resp_lock:
            rec, self._resp_busy = self._resp_busy, None
        if rec is None:
            return
        if rec.get("bl") and not rec.get("parked"):  # decided by the owner and answered; a failure stays in the backlog
            it = self.backlog_set(rec["bl"], "done" if text is not None else "open")
            if text is None and it:
                self.backlog_show(it)
        owner = text is None
        out = _t("Could not answer now. I will pass this to the owner.") if owner else text.strip() + rec.get("note", "")
        if rec.get("link") and not owner:
            out += "\n\n" + rec["link"]
        out, files = self.responder_files(out)
        self.chat_tail_add(self.chat_name(), out)
        try:
            # the model writes markdown: send_md turns it into Telegram HTML (bold, links) like console replies
            # links as «text (url)»: a chat answer never hides where a link goes
            self.tg_chat.send_md(self.cfg.responder_chat, re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", out),
                                 reply_to=rec["mid"])
        except TGError as e:
            log("responder reply failed: %s" % e)
        for path in files:
            name = os.path.basename(path)
            try:
                with open(path, "rb") as f:
                    self.tg_chat.send_document(self.cfg.responder_chat, name, f.read())
                self.chat_tail_add(self.chat_name(), "[прислан файл %s]" % name)
            except (TGError, OSError) as e:
                log("responder file failed: %s" % type(e).__name__)
                self.chat_tail_add(self.chat_name(), "[файл %s не отправился]" % name)
                try:
                    self.tg_chat.send(self.cfg.responder_chat, "Файл %s не отправился." % name, reply_to=rec["mid"])
                except TGError as e2:
                    log("responder reply failed: %s" % e2)
        if owner:
            for uid in sorted(self.cfg.allowed):
                try:
                    self.tg.send(uid, _t("📨 From the chat, %s: %s\n\nAnswer: %s") % (rec["who"], rec["q"], out))
                except TGError as e:
                    log("responder owner notice failed: %s" % e)
        try:
            os.makedirs(os.path.dirname(self.cfg.responder_log), mode=0o700, exist_ok=True)
            with open(self.cfg.responder_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "from": rec["from"], "who": rec["who"], "q": rec["q"],
                                    "a": out, "owner": owner}, ensure_ascii=False) + "\n")
        except OSError as e:
            log("responder log failed: %s" % type(e).__name__)
        self.responder_next()

    def sdk_on_event(self, ev):
        name = str(ev.get("event") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if name == "request.opened":
            sid, req = str(data.get("id") or ""), data.get("request")
            if sid == (self.store.data.get("responder") or {}).get("sid") and isinstance(req, dict):
                if self.responder_auto(sid, req):
                    return
                self.responder_waits(req)
            k = self.known.get(sid)
            if k is not None and isinstance(req, dict) and req.get("req"):  # the mini-app reads requests from here
                pend = k.setdefault("pending", [])
                if not any(isinstance(r, dict) and str(r.get("req")) == str(req["req"]) for r in pend):
                    pend.append(req)
            # the push first: a delivered one (the request is visible in the PWA) is not duplicated in Telegram
            self.sdk_show_perm(sid, req, skip=self.sdk_perm_push(sid, req))
        elif name == "request.closed":
            self._drop_pending(str(data.get("id") or ""), data.get("req"))
            self.sdk_close_perm(str(data.get("id") or ""), str(data.get("req") or ""), data.get("decision"))
        elif name == "turn.result":
            st = self.store.data.get("responder") or {}
            if st.get("sid") and data.get("id") == st["sid"]:
                ok = data.get("subtype") == "success" and not data.get("is_error") and isinstance(data.get("result"), str)
                if not ok and self.responder_codex(data):
                    return
                if not ok and isinstance(data.get("result"), str) and data["result"].strip():
                    # the API's own words (e.g. «402 Insufficient Balance») are for the owner only: the group gets the neutral text
                    for uid in sorted(self.cfg.allowed):
                        try:
                            self.tg.send(uid, "⚠️ Чат-бот не смог ответить: %s" % short(" ".join(data["result"].split()), 300))
                        except TGError as e:
                            log("responder owner notice failed: %s" % e)
                if not ok:  # budget or error: the next question starts a fresh session
                    self.responder_forget()
                if ok or data.get("subtype") != "error_max_budget_usd" or not self.responder_retry():
                    self.responder_answer(data["result"] if ok else None)
            self.sdk_turn_result(data)
        elif name in ("session.created", "session.state") and isinstance(data.get("session"), dict):
            x = data["session"]
            if (x.get("id") == (self.store.data.get("responder") or {}).get("sid") and self._resp_busy
                    and x.get("state") in ("error", "stopped", "interrupted", "budget")):
                if not self.responder_codex(x):
                    # a session that cannot go on (its transcript was cleaned up, say) gets the question once more,
                    # in a fresh one; a second failure is a plain failure
                    self.responder_forget()
                    if x.get("state") != "error" or quota_stop(x) or not self.responder_retry():
                        self.responder_drop()  # the chat session died without a result
            if is_sdk(x.get("id")):
                before = (self.known.get(x["id"]) or {}).get("sdk_state")
                self.sdk_apply(x)
                if before is not None and before != x.get("state"):
                    for uid in sorted(self.cfg.allowed):  # the live card follows the state (and its buttons)
                        self.refresh_card(uid, x["id"])
                if before is not None and SDK_STATES.get(before) != SDK_STATES.get(x.get("state")):
                    self.notify_tick()  # a state change wakes the notifier at once

    def sdk_watch_once(self):
        """One subscription; ends when the runner closes the connection (the caller resubscribes)."""
        for ev in self.sdk.subscribe(None, self.stop_event, on_start=self.sdk_reconcile):
            self.sdk_on_event(ev)

    def sdk_watch(self):
        while not self.stop_event.is_set():
            wait = 5
            if self.sdk_on():
                try:
                    self.sdk_watch_once()
                except SdkError as e:
                    self.sdk_quiet(e)
                except Exception as e:
                    log("sdk watch error: %s" % type(e).__name__)
            self.stop_event.wait(wait)

    # ---- sessions
    def list_sessions(self, show_all=False):
        if self.claude_missing():  # SDK-only: no `claude agents`, the list is SDK and herdr sessions
            sessions = []
        else:
            args = [self.cfg.claude_bin, "agents", "--json"] + (["--all"] if show_all else [])
            rc, out = self.run(args, timeout=30)
            if rc != 0:
                return None
            try:
                agents = json.loads(out)
            except ValueError:
                return None
            sessions = merge_sessions(agents, self.cfg.jobs_dir)
        hsessions = self.herdr_sessions()
        ssessions = self.sdk_sessions()
        if show_all:
            self.last_full = {x["id"] for x in sessions} | {x["id"] for x in hsessions} | {x["id"] for x in ssessions}
            self.revive_archived(sessions)
        for s in sessions:
            s["real_name"] = s["name"]
            alias = self.alias_for(s["id"])
            if alias:
                s["name"] = alias
            self.known[s["id"]] = s
        for s in sessions:  # after `known` is filled: the label needs the whole chain
            s["base_name"] = s["name"]
            s["name"] = self.chain_label(s["id"], s["name"])
        for s in hsessions:  # herdr agents have no aliases and no handoff chains
            s["real_name"] = s["base_name"] = s["name"]
            self.known[s["id"]] = s
        for s in ssessions:  # SDK sessions: the runner's name or the bot alias, no handoff chains
            s["real_name"] = s["base_name"] = s["name"]
            self.sdk_alias(s)
            self.known[s["id"]] = s
        return sort_sessions(sessions + hsessions + ssessions)

    # ---- names: display_name = alias (if any), otherwise the name from claude agents / state.json
    def alias_for(self, sid):
        rec = self.store.data.get("names", {}).get(sid)
        if rec and rec.get("alias") and rec.get("approved") and not rec.get("applied") and rec.get("name"):
            return rec["name"]
        return None

    def display_name(self, sid):
        return self.session_info(sid)["name"]

    def session_info(self, sid):
        """Fresh data for one session (state.json over what is known)."""
        if is_sdk(sid):  # no job state: what the last list or event knows
            base = dict(self.known.get(sid) or {"id": sid, "name": sid, "cwd": "", "state": "working", "detail": "",
                                                "result": None, "sessionId": "", "link": "", "updated": 0,
                                                "kind": "sdk", "source": "sdk", "sdk_state": "working"})
            base.setdefault("real_name", base["name"])
            base.setdefault("base_name", base["name"])
            return self.sdk_alias(base)
        if is_herdr(sid):  # no job state: what the last list knows
            base = dict(self.known.get(sid) or {"id": sid, "name": sid, "cwd": "", "state": "working", "detail": "",
                                                "result": None, "sessionId": "", "link": "", "updated": 0,
                                                "kind": "agent", "source": "herdr"})
            base.setdefault("real_name", base["name"])
            base.setdefault("base_name", base["name"])
            return base
        base = dict(self.known.get(sid) or {"id": sid, "name": sid, "cwd": "", "state": "working",
                                            "detail": "", "result": None, "sessionId": "", "link": "",
                                            "updated": 0})
        real = base.get("real_name") or base["name"]
        st = read_job_state(self.cfg.jobs_dir, sid)
        if st:
            out = st.get("output")
            res = out.get("result") if isinstance(out, dict) else None
            real = st.get("name") or real
            base.update({"state": st.get("state") or base["state"],
                         "detail": st.get("detail") or "",
                         "result": res if isinstance(res, str) and res.strip() else None,
                         "cwd": st.get("cwd") or base["cwd"],
                         "link": st.get("linkScanPath") or base["link"],
                         "name_source": st.get("nameSource") or base.get("name_source", ""),
                         "updated": _to_epoch(st.get("updatedAt"), None) or base["updated"]})
        base["real_name"] = real
        base["base_name"] = self.alias_for(sid) or real
        base["name"] = self.chain_label(sid, base["base_name"])
        base["state"] = live_state(base.get("agent_state") or base["state"], st or {}, base.get("busy"))
        return base

    def chain_label(self, sid, name):
        """An explicit handoff-chain label in the name: old — "🗂 name №K", current continuation — "name №K"."""
        name = strip_chain_label(name)
        if not self.store.data.get("chain_links") and not self.store.data.get("chain_manual"):
            return name
        k = self.chain_k(sid)
        if self.is_old(sid):
            return "🗂 %s №%d" % (name, k)
        if k >= 2:
            return "%s №%d" % (name, k)
        return name

    def transcript_path(self, sid):
        info = self.session_info(sid)
        return info["link"] or fallback_transcript_path(info["cwd"], info["sessionId"])

    def session_name(self, sid):
        return self.display_name(sid)

    def is_archived(self, sid):
        return sid in self.store.data.get("archived", {})

    def archived_at(self, sid):
        return self.store.data.get("archived", {}).get(sid)

    def revive_archived(self, sessions):
        """An archived session came alive again (busy process or fresh work after the mark) — the mark is removed."""
        arch = self.store.data.get("archived", {})
        for x in sessions:
            ts = arch.get(x["id"])
            if ts is None:
                continue
            busy = bool(x.get("pid")) and x.get("busy") is True
            fresh = x.get("updated", 0) > ts + 60 and x.get("state") in ("working", "blocked")
            if busy or fresh:
                arch.pop(x["id"], None)
        return arch

    def archive_session(self, sid, stop=True):
        """-> (ok, stopped, error). A bot-side mark; the conversation stays on disk."""
        stopped = False
        if stop and is_sdk(sid):
            if self.is_live(sid):
                if not self.sdk_stop(sid):
                    return False, False, _t("Could not stop the session. Try again.")
                stopped = True
        elif stop and self.is_live(sid) and not is_herdr(sid):
            rc, _ = self.run([self.cfg.claude_bin, "stop", sid], timeout=30)
            if rc != 0:
                return False, False, _t("claude stop failed")
            stopped = True
        self.store.data.setdefault("archived", {})[sid] = self.clock()
        for uid in sorted(self.cfg.allowed):
            self.set_watch(uid, sid, False)
            if sid in self.store.chat(uid)["topics"]:
                self.drop_topic(uid, sid)
        self.store.save()
        return True, stopped, None

    def unarchive_session(self, sid):
        """Back to the active list: drops the archive mark; a superseded («old») session is marked current."""
        self.store.data.get("archived", {}).pop(sid, None)
        if self.is_old(sid):
            self.store.data.setdefault("chain_manual", {})[sid] = "current"
        self.store.save()

    def done_to_archive(self):
        res = []
        for sid in self.api_ids():
            info = self.session_info(sid)
            if (info["state"] in ("done", "failed") and not self.is_old(sid) and not self.is_archived(sid)
                    and not self.pending_question(sid) and not self.is_watched(sid)
                    and self.clock() - (info.get("updated") or 0) >= ARCHIVE_IDLE):
                res.append(sid)  # «done» only ends a turn: a session touched recently is still in use
        return res

    def archive_done(self):
        sids = self.done_to_archive()
        for sid in sids:
            self.archive_session(sid, stop=False)
        return len(sids)

    def is_watched(self, sid):
        return sid in self.store.data.get("watched", [])

    def set_watch(self, chat, sid, on):
        w = self.store.data.setdefault("watched", [])
        if on and sid not in w:
            w.append(sid)
        elif not on and sid in w:
            w.remove(sid)
        c = self.store.chat(chat)
        if on and self.topics_enabled:
            self.ensure_topic(chat, sid)
        if on:
            self.mark_scan(c, sid)
        elif not on and c["session"] != sid and sid not in c["topics"]:
            fw = c["fw"].get(sid)
            if fw is not None and not fw.get("until"):
                c["fw"].pop(sid, None)
        self.store.save()
        if on and is_sdk(sid):
            self.sdk_show_pending(sid)

    def start_turn(self, c, sid):
        """A turn started from Telegram: model replies are forwarded until the turn ends."""
        fw = c["fw"].get(sid)
        if fw is None:
            self.init_fw(c, sid, until=None if self.tracked_c(c, sid) else self.clock() + 1800)
            fw = c["fw"][sid]
        elif fw.get("until"):
            fw["until"] = self.clock() + 1800
        fw["turn"], fw["turn_started"], fw["turn_w"] = True, self.clock(), False
        self.turn_forwarded.discard(sid)

    def mark_scan(self, c, sid):
        """On any open/subscribe: check whether a question is pending (even if the fw entry is old)."""
        fw = c["fw"].get(sid)
        if fw is None:
            self.init_fw(c, sid)
        else:
            fw["scan_ask"] = True

    def init_fw(self, c, sid, until=None, start=None):
        path = self.transcript_path(sid)
        off = start
        if off is None:
            try:
                off = os.path.getsize(path) if path else None
            except OSError:
                off = None
        c["fw"][sid] = {"offset": off, "seen": [], "until": until, "scan_ask": True}

    # ---- handling updates
    def authorized(self, user_id, chat_type):
        return chat_type == "private" and user_id in self.cfg.allowed

    def note_group(self, upd):
        """Responder role: every group the bot is added to or sees a message in is remembered (id -> title)."""
        mc = upd.get("my_chat_member") or {}
        chat = mc.get("chat") or (upd.get("message") or {}).get("chat") or {}
        if chat.get("type") not in ("group", "supergroup") or not isinstance(chat.get("id"), int):
            return
        groups = self.store.data.setdefault("groups", {})
        key = str(chat["id"])
        if (mc.get("new_chat_member") or {}).get("status") in ("left", "kicked"):
            changed = groups.pop(key, None) is not None
        else:
            changed = groups.get(key) != (chat.get("title") or "")
            groups[key] = chat.get("title") or ""
        if changed:
            self.store.save()

    def responder_status_text(self):
        gid = self.cfg.responder_chat
        group = (self.store.data.get("groups") or {}).get(str(gid)) or (str(gid) if gid is not None else "—")
        model = "codex" if self.cfg.responder_engine == "codex" else env_read(self.cfg.sdk_env).get("CC_SDK_MODEL") or "—"
        return _t("%s · group: %s · model: %s") % (self.chat_name(), group, model)

    def handle_responder_update(self, upd):
        """The responder role: the group, the owner's 🔐 / backlog buttons and one status line in the owner's private chat."""
        self.note_group(upd)
        if "callback_query" in upd:
            cq = upd["callback_query"]
            msg = cq.get("message") or {}
            if (self.authorized((cq.get("from") or {}).get("id"), (msg.get("chat") or {}).get("type"))
                    and (cq.get("data") or "").partition(":")[0] in ("pr", "bl")):
                self.on_callback(cq, msg)
            return
        m = upd.get("message") or upd.get("edited_message") or {}
        ctype = (m.get("chat") or {}).get("type")
        if ctype in ("group", "supergroup"):
            self.chat_update(upd)
        elif ctype == "private" and "message" in upd and self.authorized((m.get("from") or {}).get("id"), ctype):
            self.tg.send(m["chat"]["id"], self.responder_status_text())

    def handle_update(self, upd):
        if self.cfg.role == "responder":
            return self.handle_responder_update(upd)
        if self.tg_chat is self.tg:
            if self.chat_update(upd):
                return
        elif ((upd.get("message") or {}).get("chat") or {}).get("type") in ("group", "supergroup"):
            return  # groups belong to the chat bot
        if "edited_message" in upd:
            return  # edits matter only to the group responder
        if "callback_query" in upd:
            cq = upd["callback_query"]
            msg = cq.get("message") or {}
            if self.authorized((cq.get("from") or {}).get("id"), (msg.get("chat") or {}).get("type")):
                self.on_callback(cq, msg)
        elif "message" in upd:
            m = upd["message"]
            if self.authorized((m.get("from") or {}).get("id"), (m.get("chat") or {}).get("type")):
                if not (self.topics_enabled and m.get("message_thread_id")) and m.get("message_id"):
                    self._log_msg(m["chat"]["id"], {"message_id": m["message_id"]}, None)
                self.on_message(m)

    def on_message(self, m):
        chat = m["chat"]["id"]
        thread = m.get("message_thread_id") if self.topics_enabled else None
        is_panel = bool(thread) and thread == self.panel_state(chat)["thread"]
        try:
            self._on_message(m, is_panel)
        finally:
            if is_panel and m.get("message_id"):
                self.tg.delete(chat, m["message_id"])  # nothing but the panel stays in the panel topic

    @staticmethod
    def extract_attachment(m):
        """document / photo (largest) / video / audio / voice -> dict or None."""
        if isinstance(m.get("document"), dict):
            d = m["document"]
            return {"file_id": d.get("file_id"), "name": d.get("file_name") or "document",
                    "size": d.get("file_size") or 0, "mime": d.get("mime_type")}
        if isinstance(m.get("photo"), list) and m["photo"]:
            d = max((x for x in m["photo"] if isinstance(x, dict)),
                    key=lambda x: (x.get("file_size") or 0, x.get("width") or 0), default=None)
            if d:
                return {"file_id": d.get("file_id"), "name": "photo_%s.jpg" % (d.get("file_unique_id") or "x"),
                        "size": d.get("file_size") or 0, "mime": "image/jpeg"}
        for key, ext, default in (("video", "mp4", "video/mp4"), ("audio", "mp3", "audio/mpeg"),
                                  ("voice", "ogg", "audio/ogg")):
            d = m.get(key)
            if isinstance(d, dict):
                return {"file_id": d.get("file_id"),
                        "name": d.get("file_name") or "%s_%s.%s" % (key, d.get("file_unique_id") or "x", ext),
                        "size": d.get("file_size") or 0, "mime": d.get("mime_type") or default}
        return None

    def on_file_message(self, m, att, is_panel, tsid, c):
        chat = m["chat"]["id"]
        if is_panel:
            self.panel_flash(chat, _t("Send the file to a session topic."))
            return
        sid = tsid if self.topics_enabled else c["session"]
        rt = m.get("reply_to_message")
        if rt and rt.get("message_id") is not None:
            target = self.store.data.get("msgmap", {}).get("%s:%s" % (chat, rt["message_id"]))
            if target:
                sid = target
        if not sid and self.topics_enabled and not m.get("message_thread_id"):  # All: routed like text
            self.react_seen(chat, m.get("message_id"))
            self.spawn(self.route_file, chat, att, m.get("caption") or "", m.get("message_id"))
            return
        if not sid:
            self.say_temp(chat, _t("Send the file to the topic of the session you need.") if self.topics_enabled else no_session())
            return
        self.spawn(self.do_file, chat, sid, att, m.get("caption") or "", m.get("message_id"))

    def do_file(self, chat, sid, att, caption, user_mid):
        msg = self.fetch_file_message(chat, sid, att, caption)
        if msg:
            self.do_send(chat, sid, msg, user_mid)

    def inbox_folder(self, sid):
        """Stable per-session folder in ~/tg-inbox: fixed at first use so a rename does not scatter files."""
        with self.store.lock:
            dirs = self.store.data.setdefault("inbox_dirs", {})
            if sid not in dirs:
                name = safe_dirname(self.session_name(sid)) or sid
                if name in {v for k, v in dirs.items() if k != sid}:
                    name = "%s (%s)" % (name, sid[:8])
                dirs[sid] = name
                self.store.save()
            return dirs[sid]

    def new_inbox_folder(self, label):
        """A folder for a session that does not exist yet (bound to its sid after do_new)."""
        name = safe_dirname(" ".join(label.split())[:40]) or "file"  # no «…»: it is a folder name
        with self.store.lock:
            used = set(self.store.data.get("inbox_dirs", {}).values())
            cand, i = name, 1
            while cand in used:
                i += 1
                cand = "%s (%d)" % (name, i)
            return cand

    def route_file(self, chat, att, caption, user_mid):
        st = {}

        def build(sid):
            folder = self.inbox_folder(sid) if sid else self.new_inbox_folder(
                caption.strip() or os.path.splitext(att["name"])[0])
            msg = self.fetch_file_message(chat, sid, att, caption, folder)
            if msg and not sid:
                build.folder = folder
            return msg
        self.route_main(chat, ((caption or "").strip() + " " + att["name"]).strip(), user_mid, build)

    def fetch_file_message(self, chat, sid, att, caption, folder=None):
        """Download into ~/tg-inbox/<session folder> -> the text that tells the session about the file, or None (the owner is told why)."""
        folder = folder or (self.inbox_folder(sid) if sid else None)
        too_big = _t("The file is larger than 20 MB — send it via the mini-app")
        try:
            if att["size"] > TG_DL_LIMIT:
                self.say_temp(chat, too_big, sid=sid)
                return
            info = self.tg.get_file(att["file_id"])
            if (info.get("file_size") or att["size"] or 0) > TG_DL_LIMIT:
                self.say_temp(chat, too_big, sid=sid)
                return
            data = self.tg.fetch_file(info["file_path"])
            mime = att.get("mime") or mimetypes.guess_type(att["name"])[0]
            path = save_inbox(self.cfg.home, data, att["name"], self.clock(), folder)
            return file_message(path, len(data), mime, caption)
        except TGError as e:
            if "too big" in str(e).lower():
                self.say_temp(chat, too_big, sid=sid)
            else:
                log("file download failed: %s" % e)
                self.say_temp(chat, _t("Could not download the file. Try again."), sid=sid)
        except OSError as e:
            log("file save failed: %s" % type(e).__name__)
            self.say_temp(chat, _t("Could not save the file."), sid=sid)

    def _on_message(self, m, is_panel=False):
        chat = m["chat"]["id"]
        text = m.get("text")
        att = None if text else self.extract_attachment(m)
        if not text and not att:
            return
        c = self.store.chat(chat)
        thread = m.get("message_thread_id") if self.topics_enabled else None
        tsid = self.sid_of_thread(chat, thread) if thread else None
        if tsid:
            self.touch_topic(chat, tsid)
        if att and (isinstance(m.get("voice"), dict) or isinstance(m.get("audio"), dict)):
            self.on_voice_message(m, att, is_panel, tsid, c)
            return
        if att:
            self.on_file_message(m, att, is_panel, tsid, c)
            return
        kind = button_kind(text)
        if kind:
            c["pending_new"] = c["pending_own"] = c["pending_rename"] = None
            if kind == "list":
                self.cmd_panel(chat)
            elif kind == "new":
                self.new_dialog(chat)
            elif kind == "quota":
                self.send_quota(chat)
            elif kind == "clean":
                self.clean_chat(chat)
            elif is_panel:
                self.panel_flash(chat, _t("This button works inside a session topic."))
            elif kind == "cur":
                sid = tsid or c["session"]
                if sid:
                    self.send_card(chat, sid)
                else:
                    self.say_temp(chat, no_session())
            else:
                self.cmd_screen(chat, tsid)
            return
        in_all = self.topics_enabled and not thread
        path_task = ((is_panel or in_all) and text.startswith("/")
                     and os.path.isdir(os.path.expanduser(text.split(None, 1)[0])))  # "/path" or "/path task"
        if text.startswith("/") and not path_task:
            c["pending_new"] = c["pending_own"] = c["pending_rename"] = None
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@")[0].lower()
            if cmd == "/start":
                if in_all:
                    self.greet(chat)
                self.cmd_panel(chat, start=True)
            elif cmd == "/help":
                self.panel_mode(chat, "help")
                self.panel_publish(chat)
            elif cmd == "/s":
                self.cmd_panel(chat)
            elif cmd == "/new":
                if arg.strip():
                    self.cmd_new(chat, arg)
                else:
                    self.new_entry(chat)
            elif cmd == "/backlog":
                items = [x for x in self.backlog() if x["status"] == "open"]
                for it in items:
                    self.backlog_show(it, [chat])
                if not items:
                    self.say_temp(chat, _t("The backlog is empty."))
            elif cmd == "/quota":
                self.send_quota(chat)
            elif cmd == "/responder" and arg.strip() in ("on", "off"):
                self.store.data.setdefault("responder", {})["off"] = arg.strip() == "off"
                self.store.save()
                self.say_temp(chat, _t("Chat responder: %s") % arg.strip())
            elif cmd == "/pwa":
                self.cmd_pwa(chat)
            elif cmd == "/devices":
                self.cmd_devices(chat)
            elif cmd in ("/screen", "/stop") and is_panel:
                self.panel_flash(chat, _t("This command works inside a session topic."))
            elif cmd == "/screen":
                self.cmd_screen(chat, tsid)
            elif cmd == "/stop":
                sid = tsid or c["session"]
                if sid:
                    self.confirm_stop(chat, sid)
                else:
                    self.say_temp(chat, no_session())
            elif is_panel:
                self.panel_flash(chat, _t("Unknown command. Help: /help"))
            else:
                self.say_temp(chat, _t("Unknown command. Help: /help"))
            return
        if c.get("pending_own"):
            po = c["pending_own"]
            c["pending_own"] = None
            self.store.save()
            self.spawn(self.question_own, chat, po["key"], po["step"], text, m.get("message_id"))
            return
        if c.get("pending_rename"):
            sid = c["pending_rename"]["sid"]
            c["pending_rename"] = None
            self.store.save()
            self.spawn(self.manual_rename, chat, sid, text, m.get("message_id"))
            return
        if is_panel:
            self.panel_new_task(chat, text)
            return
        if thread and not tsid:
            self.tg.send_one(chat, _t("This topic is not linked to a session. Open a session via «📋 Sessions»."),
                             thread=thread)
            return
        if c.get("pending_new") is not None and not thread:
            cwd = c["pending_new"].get("cwd") or self.cfg.default_cwd
            sdk = bool(c["pending_new"].get("sdk"))
            c["pending_new"] = None
            self.store.save()
            self.spawn(self.do_new, chat, cwd, text, *((True,) if sdk else ()))
            return
        sid = tsid if self.topics_enabled else c["session"]
        rt = m.get("reply_to_message")
        if rt and rt.get("message_id") is not None:
            target = self.store.data.get("msgmap", {}).get("%s:%s" % (chat, rt["message_id"]))
            if target:
                sid = target
        if not sid and in_all:
            if not c.get("greet_mid"):
                self.greet(chat)
            self.react_seen(chat, m.get("message_id"))
            self.spawn(self.route_main, chat, text, m.get("message_id"))
            return
        if not sid:
            self.say_temp(chat, no_session())
            return
        self.spawn(self.do_send, chat, sid, text, m.get("message_id"))

    # ---- routing of the All feed
    def react_seen(self, chat, mid):
        try:
            self.tg.react(chat, mid)
        except TGError:
            pass

    def greet(self, chat):
        """The one permanent hello in All; a new one replaces the old."""
        c = self.store.chat(chat)
        old = c.get("greet_mid")
        r = self.say(chat, _t("👋 Write here — I will send it to a fitting session or start a new one. "
                              "Sessions are in topics, the panel is in «%s».") % panel_title())
        mid = r.get("message_id") if isinstance(r, dict) else None
        if mid:
            c["greet_mid"] = mid
            self.store.save()
            if old and old != mid:
                self.tg.delete(chat, old)

    def greet_missing(self):
        """At start: the hello appears in All without waiting for the first message there."""
        if not self.topics_enabled:
            return
        for uid in self.cfg.allowed:
            if not self.store.chat(uid).get("greet_mid"):
                try:
                    self.greet(uid)
                except TGError as e:
                    log("greet failed: %s" % e)

    def route_pick(self, text, sessions):
        payload = {"message": text, "sessions": [
            {"id": x["id"], "name": x.get("real_name") or x["name"], "cwd": x["cwd"],
             "task": short(x.get("task") or "", 300), "status": short(x.get("detail") or "", 160)} for x in sessions]}
        rc, out = self.run([self.cfg.claude_bin, "-p", "--model", "haiku", "--output-format", "text"], timeout=60,
                           input_text=route_prompt() + "\n" + json.dumps(payload, ensure_ascii=False))
        if rc != 0:
            log("route_pick failed: rc=%s" % rc)
            return None, None
        return parse_route(out, {x["id"] for x in sessions}, {x["cwd"] for x in sessions})

    def route_main(self, chat, text, mid, build=None):
        with self._route_lock:
            try:
                self._route(chat, text, mid, build)
            except TGError as e:
                log("route failed: %s" % e)

    def _route(self, chat, text, mid, build=None):
        """build(sid) -> the final text (None = failed, owner told); build(None) is for a new session."""
        c = self.store.chat(chat)
        last = c.get("route_last")
        if last and last.get("sid") and self.clock() - last["ts"] < ROUTE_STICKY:
            self.route_to(chat, c, last["sid"], text, mid, last.get("cwd"), build)
            return
        cwd, task = parse_task_text(text, None)
        if cwd:  # an explicit path needs no model
            if task:
                self.route_new(chat, c, cwd, task, mid, build)
            else:
                self.say_temp(chat, _t("After the path, write what needs to be done."))
            return
        sid = None
        if not self.claude_missing():
            resp = self.store.data.get("responder") or {}
            # the full list: a background session that answered and has no viewer drops out of the short one
            now = self.clock()
            sessions = [x for x in (self.list_sessions(True) or []) if x.get("source") != "herdr"
                        and x["id"] not in (resp.get("sid"), resp.get("sid_guest"))
                        and x.get("state") not in ("stopped", "failed") and not self.stopped(x["id"])
                        and not self.is_archived(x["id"])
                        and not (x.get("updated") and now - x["updated"] >= ROUTE_RECENT)]
            if sessions:
                sid, cwd = self.route_pick(text, sessions)
            log("route: %d candidates -> %s" % (len(sessions), sid or "new"))
        if sid:
            self.route_to(chat, c, sid, text, mid, next(x["cwd"] for x in sessions if x["id"] == sid), build)
        else:
            self.route_new(chat, c, cwd or self.cfg.default_cwd, text, mid, build)

    def route_to(self, chat, c, sid, text, mid, cwd, build=None):
        if build:
            text = build(sid)
            if text is None:
                return
        self.ensure_topic(chat, sid)
        ok, err = self.deliver(chat, sid, text)
        if not ok:
            c["route_last"] = None
            self.report_send_error(chat, sid, err, mid)
            return
        c["route_last"] = {"sid": sid, "ts": self.clock(), "mid": mid, "text": text, "cwd": cwd}
        self.store.save()
        self.say(chat, "💬 " + text, sid=sid)
        self.tg.delete(chat, mid)
        self.say_temp(chat, _t("→ sent to topic «%s»") % self.session_name(sid),
                      {"inline_keyboard": [[{"text": _t("🆕 As a new session"), "callback_data": "rt:%s" % mid}]]},
                      ttl=30)

    def route_new(self, chat, c, cwd, task, mid, build=None):
        """mid=None is a redo from the button: the source message is already gone and the stickiness is reset."""
        if build:
            task = build(None)
            if task is None:
                return
        eng = self.engine_armed(c)
        c["new_engine"] = None
        sid = self.do_new(chat, cwd, task, sdk=eng == "sdk" or self.claude_missing())
        if not sid:  # the source message stays: nothing was lost
            return
        if build and getattr(build, "folder", None):
            with self.store.lock:
                self.store.data.setdefault("inbox_dirs", {})[sid] = build.folder
        c["route_last"] = {"sid": sid, "ts": self.clock(), "mid": mid, "text": task, "cwd": cwd} if mid else None
        self.store.save()
        self.say(chat, "💬 " + task, sid=sid)  # the card does not show the task
        if mid:
            self.tg.delete(chat, mid)
            self.say_temp(chat, _t("→ opened in topic «%s»") % self.session_name(sid), ttl=30)

    def route_redo(self, chat, rl, btn_mid):
        with self._route_lock:
            try:
                if btn_mid:
                    self.tg.delete(chat, btn_mid)
                self.route_new(chat, self.store.chat(chat), rl.get("cwd") or self.cfg.default_cwd, rl["text"], None)
            except TGError as e:
                log("route redo failed: %s" % e)

    # ---- voice input (speech-to-text runner)
    def stt_on(self):
        return self.stt is not None and self.stt.enabled()

    def stt_quiet(self, e):
        """A broken runner connection is logged at most once per SDK_QUIET."""
        now = self.clock()
        if now - self._stt_logged >= SDK_QUIET:
            self._stt_logged = now
            log("stt: %s" % e.code)

    def stt_ready(self, fresh=False):
        """The runner answers and has at least one ready engine (the microphone is offered only then)."""
        now = self.clock()
        if not fresh and now - self._stt_ready[0] < STT_READY_TTL:
            return self._stt_ready[1]
        ok = False
        if self.stt_on():
            try:
                engines = self.stt.call("stt.info").get("engines")
                ok = any(isinstance(x, dict) and x.get("ready") is True for x in (engines or []))
            except HerdrError as e:
                self.stt_quiet(e)
        self._stt_ready = (now, ok)
        return ok

    def stt_lang(self):
        return self.cfg.stt_lang or LANG

    def stt_bytes(self, data, ext=".ogg"):
        """Recognises an audio recording through a temporary file (always removed).
        -> (code, result): code is ok | unavailable | empty | error. Neither the text nor the path is logged."""
        if not self.stt_ready(True):
            return "unavailable", None
        d = tempfile.mkdtemp(prefix="zidos-pager-stt-")
        try:
            path = os.path.join(d, "voice" + ext)
            with open(path, "wb") as f:
                f.write(data)
            res = self.stt.call("stt.transcribe", {"path": path, "lang": self.stt_lang()}, timeout=STT_TIMEOUT)
        except HerdrError as e:
            log("stt failed: %s" % e.code)
            return "error", None
        except OSError as e:
            log("stt temp file failed: %s" % type(e).__name__)
            return "error", None
        finally:
            shutil.rmtree(d, ignore_errors=True)
        try:
            log("stt: %.1fs of audio, engine %s, took %.1fs" % (
                float(res.get("seconds") or 0), str(res.get("engine") or "?")[:20], float(res.get("took") or 0)))
        except (TypeError, ValueError):
            pass
        if not str(res.get("text") or "").strip():
            return "empty", res
        return "ok", res

    @staticmethod
    def stt_problem(code):
        if code == "unavailable":
            return _t("Voice input is not set up: start the zidos-pager-stt service (see README)")
        if code == "empty":
            return _t("No speech recognized")
        return _t("Could not recognize the speech")

    def on_voice_message(self, m, att, is_panel, tsid, c):
        chat = m["chat"]["id"]
        sid = None
        if not is_panel:
            sid = tsid if self.topics_enabled else c["session"]
            rt = m.get("reply_to_message")
            if rt and rt.get("message_id") is not None:
                target = self.store.data.get("msgmap", {}).get("%s:%s" % (chat, rt["message_id"]))
                if target:
                    sid = target
            if not sid and self.topics_enabled and not m.get("message_thread_id"):  # All: the text is routed on «Send»
                self.spawn(self.do_voice, chat, None, att, m.get("message_id"), True)
                return
            if not sid:
                self.say_temp(chat, _t("Send the voice message to the topic of the session you need.")
                              if self.topics_enabled else no_session())
                return
        self.spawn(self.do_voice, chat, sid, att, m.get("message_id"))

    def voice_note(self, chat, sid, text, route=False):
        if route:
            self.say_temp(chat, text)
        elif sid is None:
            self.panel_flash(chat, text)
        else:
            self.say_temp(chat, text, sid=sid)

    def do_voice(self, chat, sid, att, user_mid, route=False):
        """The voice message of the owner -> text under it with «📨 Send» / «✖». sid None = the panel topic."""
        def react(emoji):
            if user_mid:
                try:
                    self.tg.react(chat, user_mid, emoji)
                except TGError:
                    pass  # e.g. the message in the panel topic is already deleted
        react("✍")
        try:
            if att["size"] > TG_DL_LIMIT:
                self.voice_note(chat, sid, route=route, text=_t("The file is larger than 20 MB — send it via the mini-app"))
                return
            if not self.stt_ready(True):
                self.voice_note(chat, sid, route=route, text=self.stt_problem("unavailable"))
                return
            info = self.tg.get_file(att["file_id"])
            if (info.get("file_size") or att["size"] or 0) > TG_DL_LIMIT:
                self.voice_note(chat, sid, route=route, text=_t("The file is larger than 20 MB — send it via the mini-app"))
                return
            data = self.tg.fetch_file(info["file_path"])
            ext = os.path.splitext(att["name"])[1].lower()
            code, res = self.stt_bytes(data, ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ".ogg")
            if code != "ok":
                self.voice_note(chat, sid, route=route, text=self.stt_problem(code))
                return
            self.voice_card(chat, sid, str(res["text"]).strip(), user_mid, route)
        except TGError as e:
            if "too big" in str(e).lower():
                self.voice_note(chat, sid, route=route, text=_t("The file is larger than 20 MB — send it via the mini-app"))
            else:
                log("voice download failed: %s" % e)
                self.voice_note(chat, sid, route=route, text=_t("Could not download the file. Try again."))
        finally:
            react(None)

    def voice_card(self, chat, sid, text, user_mid, route=False):
        text = text[:VOICE_TEXT_MAX]
        with self.store.lock:
            d = self.store.data
            d["vseq"] = d.get("vseq", 0) + 1
            n = self.qkey(chat, sid or "", "voice-%d" % d["vseq"], "v")
            voices = d.setdefault("voices", {})
            voices[n] = {"chat": chat, "sid": sid, "text": text, "umid": user_mid, "route": route}
            while len(voices) > VOICE_KEEP:
                del voices[next(iter(voices))]
            self.store.save()
        markup = {"inline_keyboard": [[{"text": _t("📨 Send"), "callback_data": "vs:" + n},
                                       {"text": "✖", "callback_data": "vx:" + n}]]}
        if route:
            self.say(chat, "🎙 " + text, markup, reply_to=user_mid)
        elif sid is None:  # the panel topic: the owner's own message is removed there, so no reply
            self.tg.send(chat, "🎙 " + text, markup, thread=self.panel_state(chat)["thread"])
        else:
            self.say(chat, "🎙 " + text, markup, sid=sid, reply_to=user_mid)

    def voice_send(self, chat, mid, n):
        with self.store.lock:
            voices = self.store.data.setdefault("voices", {})
            rec = voices.get(n)
            if rec is None or rec["chat"] != chat:
                rec = None
            else:
                del voices[n]  # a second press finds nothing: no double send
        if rec is None:
            self.popup(chat, _t("This voice message is no longer available"))
            return
        sid = rec["sid"]
        if rec.get("route"):  # All: the card goes, the voice message is routed like text and leaves All
            if mid:
                self.tg.delete(chat, mid)
            self.popup(chat, _t("Sent"))
            self.route_main(chat, rec["text"], rec.get("umid"))
            return
        if sid is None:  # the panel topic: the text is the task of a new session
            if mid:
                self.tg.delete(chat, mid)
            self.panel_new_task(chat, rec["text"])
            self.popup(chat, _t("Sent"))
            return
        ok, err = self.deliver(chat, sid, rec["text"])
        if not ok:
            with self.store.lock:
                self.store.data["voices"][n] = rec  # the buttons stay: it can be sent again
            self.report_send_error(chat, sid, err)
            return
        if rec.get("umid"):
            self.reply_to[(chat, sid)] = rec["umid"]
        if mid:
            try:
                self.tg.edit_markup(chat, mid)
                self.tg.edit(chat, mid, _t("🎙 %s · ✔ sent") % rec["text"])
            except TGError as e:
                log("voice mark failed: %s" % e)
        self.popup(chat, _t("Sent"), sid=sid)

    def voice_cancel(self, chat, mid, n):
        with self.store.lock:
            rec = self.store.data.setdefault("voices", {}).get(n)
            if rec is not None and rec["chat"] == chat:
                del self.store.data["voices"][n]
            else:
                rec = None
        if mid:
            self.tg.delete(chat, mid)
        if rec and rec.get("route") and rec.get("umid"):  # All stays clean: the voice message goes with its card
            self.tg.delete(chat, rec["umid"])

    # ---- sending to a session
    def webapp_active(self, sid):
        t = self.webapp_touch.get(sid)
        return t is not None and self.clock() - t <= WEBAPP_ACTIVE

    def webapp_recent(self, sid):
        t = self.webapp_touch.get(sid)
        return t is not None and self.clock() - t < WEBAPP_QUIET

    def deliver(self, chat, sid, text, origin="chat"):
        if origin == "webapp":
            # the owner will see the answer in the mini-app: no turn to forward, no status, no typing
            self.webapp_touch[sid] = self.clock()
            return self.injector(sid, text)
        c = self.store.chat(chat)
        fw = c["fw"].get(sid)
        if fw is None:
            self.init_fw(c, sid, until=None if self.tracked_c(c, sid) else self.clock() + 1800)
        elif fw.get("until"):
            fw["until"] = self.clock() + 1800
        self.store.save()
        ok, err = self.injector(sid, text)
        if ok:
            self.start_turn(c, sid)
            self.store.save()
            self.status[(chat, sid)] = {"sid": sid, "since": self.clock(), "mid": None, "text": None,
                                 "edited": 0, "typed": 0, "seen_working": False}
        return ok, err

    def report_send_error(self, chat, sid, err, reply_to=None):
        screen_btn = {"inline_keyboard": [[{"text": _t("📺 Screen"), "callback_data": "scr:" + sid}]]}
        if is_sdk(sid):  # no screen to look at: the error text says what to do
            self.say_temp(chat, _t("⚠️ Not sent. ") + err, sid=sid, reply_to=reply_to)
            return
        if err == ERR_MENU:
            q = self.pending_question(sid)
            if q:
                self.say_temp(chat, _t("The session is waiting for an answer to a question — pick an option or "
                                       "✏️ Your own answer."), sid=sid, reply_to=reply_to)
                self.post_question(chat, q)
            else:
                self.say(chat, _t("The session is waiting for an answer to a question — pick an option on "
                                  "the screen."),
                         screen_btn, sid=sid, reply_to=reply_to)
            return
        self.say(chat, _t("⚠️ Not sent. ") + err, screen_btn, sid=sid, reply_to=reply_to)

    def do_send(self, chat, sid, text, user_mid=None):
        try:
            ok, err = self.deliver(chat, sid, text)
            if ok:
                if user_mid:
                    self.reply_to[(chat, sid)] = user_mid
                try:
                    if not user_mid:
                        raise TGError("no message")
                    self.tg.react(chat, user_mid)
                except TGError:
                    self.say(chat, "📨 → " + self.session_name(sid), sid=sid)
            else:
                self.report_send_error(chat, sid, err, user_mid)
        except TGError as e:
            log("send reply failed: %s" % e)
        except Exception as e:
            log("do_send error: %s" % type(e).__name__)
            try:
                self.say(chat, _t("⚠️ Could not send. Open 📺 Screen."), sid=sid, reply_to=user_mid)
            except TGError:
                pass

    def quick_send(self, chat, mid, sid, word, orig):
        ok, err = self.deliver(chat, sid, word)
        if ok:
            self.tg.edit_markup(chat, mid)
            self.tg.edit(chat, mid, _t("%s\n"
                                       "→ sent: %s") % (orig, word))
        else:
            self.report_send_error(chat, sid, err)

    # ---- list and card
    def make_current(self, chat, sid, start=None):
        c = self.store.chat(chat)
        old = c["session"]
        c["session"] = sid
        if old and old != sid:
            ofw = c["fw"].get(old)
            if (ofw is not None and not self.is_watched(old) and not ofw.get("turn")
                    and old not in c["topics"]):
                c["fw"].pop(old, None)
        if not (self.is_watched(sid) and sid in c["fw"] and start is None):
            self.init_fw(c, sid, start=start)
        c["fw"][sid]["until"] = None
        self.mark_scan(c, sid)
        self.store.save()

    def is_asking(self, sid, info):
        """An open AskUserQuestion on a live session: shown as «waiting for you», same rule as the panel group."""
        return bool(not self.is_old(sid) and not self.is_archived(sid) and info["state"] in ("working", "blocked")
                    and self.session_ask(info))

    def card_text(self, sid, current=False):
        info = self.session_info(sid)
        now = self.clock()
        asking = self.is_asking(sid, info)
        shown = "blocked" if asking else info["state"]
        mark = "" if info["name"].startswith("🗂 ") else ("❓" if asking else EMOJI.get(info["state"], "⚪")) + " "
        lines = ["%s%s%s" % ("▶ " if current else "", mark, info["name"]),
                 "%s%s" % (state_word(shown),
                           (" · " + ago(info["updated"], now)) if ago(info["updated"], now) else "")]
        if self.is_old(sid):
            kid = self.chain_child(sid)
            lines.insert(0, (_t("🗂 Old: continued in «%s»") % self.display_name(kid)) if kid else _t("🗂 Old"))
        if self.is_archived(sid):
            lines.insert(0, _t("📦 Archived"))
        elif self.chain_parent(sid):
            lines.append(_t("↪ Current (№%d), previous: «%s»") % (self.chain_k(sid),
                                                                 self.display_name(self.chain_parent(sid))))
        cost = sdk_cost_line(info) if is_sdk(sid) else ""
        if cost:
            lines.append(cost)
        if is_sdk(sid) and info.get("always"):
            lines.append("♾ " + ", ".join(info["always"][:6]))
        detail = humanize_detail(info["detail"])
        if detail:
            lines += ["", detail]
        if info["result"] and info["result"] not in (info["detail"], detail):
            lines += ["", _t("Result: ") + info["result"]]
        path = self.transcript_path(sid)
        last = tail_texts(path, 1) if path else []
        if last:
            t = last[-1][1]
            lines += ["", _t("Last reply"), t if len(t) <= 900 else t[:899] + "…"]
        lines += ["", "%s · %s" % (tilde(info["cwd"]), sid)]
        return "\n".join(lines)

    def card_key(self, chat, sid):
        """Cards of a topic and of the main chat are different messages."""
        th = self._th(chat, sid)
        return "%s:%s" % (th or "main", sid)

    def card_view(self, chat, sid):
        topic = self._th(chat, sid) is not None
        cur = topic or self.store.chat(chat)["session"] == sid
        text = self.card_text(sid, cur and not topic)
        markup = self.card_mk(sid, cur, self.is_watched(sid), topic, self.is_old(sid), self.is_archived(sid))
        return text, markup

    def send_card(self, chat, sid):
        topic = self._th(chat, sid) is not None
        text, markup = self.card_view(chat, sid)
        single = not (topic or not self.topics_enabled)  # single() already removes the previous card of the chat
        key = self.card_key(chat, sid)
        if not single:
            r = self.say(chat, text, markup, sid=sid)
        else:
            r = self.single(chat, "card", text, markup, sid=sid)
        # one card per session in the chat (and place): the new one replaces the old
        mid = r.get("message_id") if isinstance(r, dict) else None
        if mid:
            cards = self.store.chat(chat)["cards"]
            prev = cards.get(key)
            put_capped(cards, key, mid)
            self.store.save()
            if prev and prev != mid and not single:
                self.tg.delete(chat, prev)

    def refresh_card(self, chat, sid):
        """Silently brings the live card up to date (no new message)."""
        mid = self.store.chat(chat)["cards"].get(self.card_key(chat, sid))
        if not mid:
            return
        try:
            text, markup = self.card_view(chat, sid)
            self.tg.edit(chat, mid, text, markup)
        except TGError as e:
            log("card refresh failed: %s" % e)

    # ---- button presses
    def on_callback(self, cq, msg):
        cb = {"id": cq["id"], "done": False}
        prev = getattr(self._cbl, "cur", None)
        self._cbl.cur = cb
        try:
            self._on_callback(cq, msg, cb)
        finally:
            self._cbl.cur = prev
            if not cb.get("deferred"):
                self.cb_finish(cb)

    def _on_callback(self, cq, msg, cb):
        chat = msg["chat"]["id"]
        mid = msg.get("message_id")
        parts = (cq.get("data") or "").split(":")
        act = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        toast = None
        if act == "bl" and len(parts) == 3:
            self.backlog_press(chat, arg, parts[2])
            return
        if act == "watch" and arg:
            on = not self.is_watched(arg)
            self.set_watch(chat, arg, on)
            toast = _t("Notifications on") if on else _t("Notifications off")
            if mid:
                cur = self.store.chat(chat)["session"] == arg
                self.tg.edit_markup(chat, mid, self.card_mk(arg, cur or self._th(chat, arg) is not None, on,
                                                           self._th(chat, arg) is not None))
        if act not in POPUP_ACTS:  # the others answer at once: they can be slow and have nothing to say
            self.cb_finish(cb, toast)
        if act == "s" and arg:
            if self.topics_enabled:
                self.open_in_topic(chat, arg)
            else:
                self.make_current(chat, arg)
                self.send_card(chat, arg)
                self.panel_publish(chat)
        elif act == "cur" and arg:
            self.make_current(chat, arg)
            if mid:
                self.tg.edit_markup(chat, mid, self.card_mk(arg, True, self.is_watched(arg),
                                                           self._th(chat, arg) is not None))
            self.popup(chat, _t("Write now — it goes to «%s».") % self.session_name(arg), sid=arg)
        elif act == "go" and arg:
            if self.topics_enabled:
                self.ensure_topic(chat, arg)
            else:
                self.make_current(chat, arg)
            self.popup(chat, _t("Write here — it goes to «%s».") % self.session_name(arg), sid=arg)
        elif act == "tpc" and arg:
            self.say(chat, _t("Close the topic «%s»? The session keeps running.") % self.session_name(arg),
                     {"inline_keyboard": [[{"text": _t("Yes, close"), "callback_data": "tpd:" + arg},
                                           {"text": _t("Cancel"), "callback_data": "cl"}]]}, sid=arg)
        elif act == "tpd" and arg:
            if mid:
                self.tg.delete(chat, mid)
            self.drop_topic(chat, arg)
        elif act == "pm" and arg in ("list", "quota", "help", "clean", "names", "arch"):
            self.panel_mode(chat, arg)
            if arg == "names":
                self.panel_state(chat)["npage"] = 0
                self.start_generation(force=True)
            self.panel_publish(chat)
        elif act == "rt" and arg.isdigit():
            # shortcut: only the last route can be redone as a new session, keep a per-message map if owners ask
            c = self.store.chat(chat)
            rl = c.get("route_last")
            if not rl or rl.get("mid") != int(arg):
                self.popup(chat, _t("This button is outdated."))
                return
            c["route_last"] = None
            self.store.save()
            self.spawn(self.route_redo, chat, rl, mid)
        elif act == "nm" and arg:
            if arg == "all":
                n = 0
                for rec in self.store.data.get("names", {}).values():
                    if rec.get("name") and not rec.get("skip") and not rec.get("approved"):
                        rec["approved"] = True
                        n += 1
                self.store.save()
                self.names_tick()
                self.panel_flash(chat, _t("Applying names: %d") % n)
            elif arg == "auto":
                self.store.data["auto_names"] = not self.store.data.get("auto_names")
                for rec in self.store.data.get("names", {}).values():
                    if self.store.data["auto_names"] and rec.get("name") and not rec.get("skip"):
                        rec["approved"] = True
                self.store.save()
                self.panel_mode(chat, "names")
                self.panel_publish(chat)
            elif arg.isdigit():
                self.panel_state(chat)["npage"] = int(arg)
                self.panel_mode(chat, "names")
                self.panel_publish(chat)
        elif act == "cm" and arg:
            m = self.store.data.setdefault("chain_manual", {})
            m[arg] = "current" if self.is_old(arg) else "old"
            self.store.save()
            if mid:
                topic = self._th(chat, arg) is not None
                cur = topic or self.store.chat(chat)["session"] == arg
                self.tg.edit_markup(chat, mid, self.card_mk(arg, cur, self.is_watched(arg), topic, self.is_old(arg),
                                                           self.is_archived(arg)))
            self.panel_publish(chat)
        elif act == "ch" and arg:
            head = self.chain_head(arg)
            if self.topics_enabled:
                self.open_in_topic(chat, head)
            else:
                self.make_current(chat, head)
                self.send_card(chat, head)
        elif act == "rn" and is_sdk(arg):
            self.popup(chat, _t("Not available for SDK sessions"), sid=arg)
        elif act == "rn" and arg:
            self.store.chat(chat)["pending_rename"] = {"sid": arg}
            self.store.save()
            r = self.tg.send_one(chat, _t("New name for «%s»") % self.session_name(arg),
                                 {"force_reply": True, "input_field_placeholder": _t("up to 28 characters")},
                                 thread=self._th(chat, arg))
            if isinstance(r, dict) and r.get("message_id"):
                self.temp.append((self.clock() + 120, chat, r["message_id"]))
        elif act == "pay":
            n = self.archive_done()
            self.panel_state(chat)["show_all"] = True
            self.panel_flash(chat, _t("Archived: %d") % n)
        elif act == "pwa":
            self.cmd_pwa(chat)
        elif act == "dvx" and arg:
            for h in [h for h, d in self.store.data.get("devices", {}).items()
                      if d["uid"] == chat and h.startswith(arg)]:
                self.remove_device(h)
            text, markup = self.devices_view(chat)
            if mid:
                self.tg.edit(chat, mid, text, markup or None)
        elif act == "dvall":
            for h in [h for h, d in self.store.data.get("devices", {}).items() if d["uid"] == chat]:
                self.remove_device(h)
            if mid:
                self.tg.edit(chat, mid, _t("You are signed out on all devices. Add again: /pwa"))
        elif act == "arc" and arg:
            self.say(chat, _t("Stop and archive? The conversation is kept."),
                     {"inline_keyboard": [[{"text": _t("Yes"), "callback_data": "arcy:" + arg},
                                           {"text": _t("Cancel"), "callback_data": "cl"}]]}, sid=arg)
        elif act == "arcy" and arg:
            name = self.display_name(arg)
            ok, _, err = self.archive_session(arg, stop=True)
            if mid:
                self.tg.delete(chat, mid)
            self.panel_flash(chat, (_t("🗂 Archived: %s") % name) if ok else (_t("Failed: %s") % err))
        elif act == "uar" and arg:
            self.unarchive_session(arg)
            if mid:
                topic = self._th(chat, arg) is not None
                cur = topic or self.store.chat(chat)["session"] == arg
                self.tg.edit_markup(chat, mid, self.card_mk(arg, cur, self.is_watched(arg), topic,
                                                           self.is_old(arg), False))
            self.panel_publish(chat)
        elif act == "pa":
            self.panel_state(chat)["show_all"] = arg == "1"
            self.panel_state(chat)["page"] = 0
            self.panel_mode(chat, "list")
            self.panel_publish(chat)
        elif act == "pp" and arg.isdigit():
            self.panel_state(chat)["page"] = int(arg)
            self.panel_mode(chat, "list")
            self.panel_publish(chat)
        elif act == "pcy":
            self.clean_done_topics(chat)
        elif act == "ngo":
            self.panel_launch(chat)
        elif act == "ngs":
            self.panel_launch(chat, sdk=True)
        elif act == "ndl":
            c = self.store.chat(chat)
            c["new_dirs"] = self.recent_dirs()
            self.panel_mode(chat, "dirs")
            self.store.save()
            self.panel_publish(chat)
        elif act == "nb":
            self.panel_mode(chat, "new")
            self.panel_publish(chat)
        elif act == "nt" and arg.isdigit():
            dirs = self.store.chat(chat)["new_dirs"]
            if int(arg) < len(dirs):
                self.panel_launch(chat, dirs[int(arg)])
            else:
                self.panel_flash(chat, _t("This button is outdated."))
        elif act == "ncx":
            self.store.chat(chat)["new_engine"] = None
            self.panel_mode(chat, "list")
            self.panel_publish(chat)
        elif act == "neg" and arg in ("sub", "sdk"):
            self.set_engine(chat, arg)
        elif act == "lst":
            if self.topics_enabled:
                self.popup(chat, hint_main())
            else:
                self.cmd_panel(chat)
        elif act == "new":
            self.new_entry(chat)
        elif act == "nd" and arg.isdigit():
            self.pick_dir(chat, mid, int(arg))
        elif act == "nx":
            self.store.chat(chat)["pending_new"] = None
            self.store.chat(chat)["new_sdk"] = False
            self.store.save()
            if mid:
                self.tg.edit(chat, mid, _t("Cancelled."))
        elif act == "last" and arg:
            path = self.transcript_path(arg)
            texts = tail_texts(path, 3) if path else []
            if not texts:
                self.popup(chat, _t("No model replies yet."), sid=arg)
            for _, t in texts:
                self.say_md(chat, t, header="📜 " + self.session_name(arg), sid=arg)
        elif act == "scr" and arg:
            self.spawn(self.show_screen, chat, arg, None)
        elif act == "key" and len(parts) == 3:
            self.spawn_cb(self.press_key, chat, arg, parts[2], mid)
        elif act == "vs" and arg:
            self.spawn_cb(self.voice_send, chat, mid, arg)
        elif act == "vx" and arg:
            self.voice_cancel(chat, mid, arg)
        elif act == "q" and arg:
            self.spawn_cb(self.question_press, chat, mid, arg)
        elif act == "pr" and arg:
            self.spawn_cb(self.sdk_perm_press, chat, mid, arg)
        elif act in ("sdb", "sdc", "sdi") and is_sdk(arg):
            self.spawn_cb(self.sdk_button, chat, mid, act, arg)
        elif act == "nds":
            self.store.chat(chat)["new_sdk"] = True
            self.store.save()
            if mid:
                self.tg.edit(chat, mid, _t("Where to start the new session? ⚡ via SDK"),
                             self.new_dialog_markup(self.store.chat(chat), False))
        elif act == "cl":
            if mid:
                self.tg.delete(chat, mid)
        elif act == "qk" and len(parts) == 3 and parts[2] in ("y", "n"):
            self.spawn(self.quick_send, chat, mid, arg, _t("yes") if parts[2] == "y" else _t("no"), msg.get("text") or "")
        elif act == "pk" and arg and len(parts) == 4 and parts[2].isdigit():
            self.spawn_cb(self.perm_press, chat, mid, arg, int(parts[2]), parts[3])
        elif act == "stp" and arg:
            self.confirm_stop(chat, arg)
        elif act == "stpy" and arg:
            if self.stop_session(arg):
                if mid:
                    self.tg.delete(chat, mid)  # the confirmation has done its job
                self.popup(chat, _t("⏹ Stopped."), sid=arg)
                if arg in self.store.chat(chat)["topics"]:
                    self.drop_topic(chat, arg)
            else:
                self.popup(chat, _t("Could not stop the session. Try again."), sid=arg, alert=True)

    def confirm_stop(self, chat, sid):
        self.say(chat, _t("Stop «%s»?") % self.session_name(sid),
                 {"inline_keyboard": [[{"text": _t("Yes, stop"), "callback_data": "stpy:" + sid},
                                       {"text": _t("Cancel"), "callback_data": "cl"}]]}, sid=sid)

    # ---- new session
    def new_entry(self, chat):
        if not self.topics_enabled:
            self.new_dialog(chat)
            return
        c = self.store.chat(chat)
        c["new_engine"] = None
        if self.claude_missing():  # only one way to start: SDK, no question
            if self.sdk_ready(True):
                self.set_engine(chat, "sdk")
            else:
                self.panel_flash(chat, no_backend_text())
        elif self.sdk_ready(True):
            self.panel_mode(chat, "engine")
            self.store.save()
            self.panel_publish(chat)
        else:
            self.panel_flash(chat, _t("To start a new session, write the task in this topic."))

    def engine_armed(self, c):
        """The engine chosen for the next task text, or None when nothing is armed or it has expired."""
        eng = c.get("new_engine")
        return eng if eng and self.clock() - c.get("new_engine_at", 0) <= NEW_ENGINE_TTL else None

    def set_engine(self, chat, engine):
        """The next task written in the panel topic starts at once, with this engine."""
        c = self.store.chat(chat)
        c["new_engine"], c["new_engine_at"] = engine, self.clock()
        self.store.save()
        self.panel_flash(chat, _t("Write the task in this topic."))

    def new_dialog(self, chat):
        c = self.store.chat(chat)
        dirs = [self.cfg.default_cwd]
        for s in (self.list_sessions(True) or []):
            if s["cwd"] and s["cwd"] not in dirs:
                dirs.append(s["cwd"])
            if len(dirs) >= 6:
                break
        c["new_dirs"] = dirs[:6]
        c["pending_new"] = None
        c["new_sdk"] = False
        self.store.save()
        self.say(chat, _t("Where to start the new session?"),
                 self.new_dialog_markup(c, self.sdk_ready(True) and not self.claude_missing()))

    def new_dialog_markup(self, c, sdk_btn):
        rows = [[{"text": "📁 " + tilde(d), "callback_data": "nd:%d" % i}] for i, d in enumerate(c["new_dirs"])]
        if sdk_btn:
            rows.append([{"text": _t("⚡ Via SDK (API key)"), "callback_data": "nds"}])
        rows.append([{"text": _t("✖ Cancel"), "callback_data": "nx"}])
        return {"inline_keyboard": rows}

    def pick_dir(self, chat, mid, idx):
        c = self.store.chat(chat)
        if idx >= len(c["new_dirs"]):
            self.popup(chat, _t("This button is outdated. Press «➕ New» again."))
            return
        cwd = c["new_dirs"][idx]
        c["pending_new"] = {"cwd": cwd, "sdk": bool(c.get("new_sdk"))}
        self.store.save()
        if mid:
            self.tg.edit(chat, mid, _t("Folder: ") + tilde(cwd) + (_t(" ⚡ via SDK") if c.get("new_sdk") else ""))
        self.tg.send_one(chat, _t("Describe the task in one message."),
                         {"force_reply": True, "input_field_placeholder": _t("What to do?")})

    def manual_rename(self, chat, sid, text, user_mid=None):
        status = self.apply_name(sid, text, manual=True)
        new = clean_name(text)
        if status == "empty":
            msg = _t("The name is empty — left as it was.")
        elif status == "applied":
            msg = _t("Name updated: «%s»") % new
        elif status in ("alias", "failed"):
            msg = _t("The name «%s» is shown in the bot; renaming inside the session failed — "
                     "will retry.") % new
        else:
            msg = _t("The name «%s» will apply in a few seconds.") % new
        self.say_temp(chat, msg, sid=sid)
        if user_mid:
            self.temp.append((self.clock() + TEMP_TTL, chat, user_mid))

    def cmd_new(self, chat, arg):
        cwd, text = parse_new_args(arg, self.cfg.default_cwd)
        if not text:
            self.new_dialog(chat)
            return
        self.spawn(self.do_new, chat, cwd, text)

    def do_new(self, chat, cwd, text, sdk=False):
        sdk = sdk or self.claude_missing()  # without Claude Code every new session is an SDK one
        try:
            if sdk:  # started in the SDK runner (API key); the runner names the session
                try:
                    x = self.sdk.call("session.start", {"cwd": cwd, "prompt": text}, timeout=15.0).get("session")
                except (SdkError, AttributeError) as e:
                    self.panel_flash(chat, "⚠️ " + (sdk_err_text(e) if isinstance(e, SdkError)
                                                    else _t("The SDK runner is not responding.")))
                    return None
                if not isinstance(x, dict) or not is_sdk(x.get("id")):
                    self.panel_flash(chat, _t("Could not create the session. Check that Claude Code starts."))
                    return None
                sid = x["id"]
                self.sdk_apply(x)
            else:
                # leading space so that text like "-x ..." is not parsed as a claude flag
                prompt = " " + text if text.startswith("-") else text
                rc, out = self.run([self.cfg.claude_bin, "--bg", "--agent", "claude", "--model", "opus", prompt],
                                   timeout=90, cwd=cwd)
                m = re.search(r"backgrounded · ([0-9a-f]{8})(?: · (.+))?", out or "")
                if rc != 0 or not m:
                    self.panel_flash(chat, _t("Could not create the session. Check that Claude Code starts."))
                    return
                sid = m.group(1)
                self.known.setdefault(sid, {"id": sid, "name": (m.group(2) or sid).strip(), "cwd": cwd,
                                            "state": "working", "detail": "", "result": None,
                                            "sessionId": "", "link": "", "updated": self.clock()})
            c = self.store.chat(chat)
            if self.topics_enabled:
                self.init_fw(c, sid, start=0)
                self.ensure_topic(chat, sid)
            else:
                self.make_current(chat, sid, start=0)
            self.set_watch(chat, sid, True)
            self.start_turn(c, sid)
            self.store.save()
            self.send_card(chat, sid)  # the only message: the card (in the topic when there are topics)
            self.panel_flash(chat, _t("🆕 started: %s (🔔 notifications on)") % self.session_name(sid))
            if not self.claude_missing():
                self.spawn(self.name_new_session, sid, cwd, text)
            return sid
        except TGError as e:
            log("do_new tg error: %s" % e)
        return None

    # ---- screen
    def cmd_screen(self, chat, sid=None):
        sid = sid or self.store.chat(chat)["session"]
        if not sid:
            self.say_temp(chat, no_session())
            return
        self.spawn(self.show_screen, chat, sid, None)

    def screen_text(self, sid):
        if is_sdk(sid):
            return "⚠️ " + _t("Not available for SDK sessions")
        if is_herdr(sid):
            ok, text = self.herdr_screen(sid)
            pane = trim_screen(text) if ok else ""
            return pane or ("⚠️ " + text if not ok else _t("(screen is empty)"))
        ok, err = self.tmux.ensure(sid)
        if ok:
            self.tmux.rewidth(sid)  # the owner looks at the screen: never show a garbled one
        pane = trim_screen(self.tmux.capture(sid))
        if not ok:
            return "⚠️ " + err + ("\n\n" + pane if pane else "")
        return pane or _t("(screen is empty)")

    def show_screen(self, chat, sid, edit_id):
        try:
            text = self.screen_text(sid)
            if edit_id:
                self.tg.edit(chat, edit_id, text, screen_markup(sid), html="<pre>%s</pre>" % esc(text))
            else:
                self.say_screen(chat, text, sid)
        except TGError as e:
            log("screen failed: %s" % e)

    def press_key(self, chat, sid, key, mid):
        try:
            if key in KEYS:
                ok, err = self.tmux.send_key(sid, KEYS[key])
                if not ok:
                    self.popup(chat, "⚠️ " + err, sid=sid, alert=True)
                    return
                self.sleep(1.0)
            self.show_screen(chat, sid, mid)
        except TGError as e:
            log("key failed: %s" % e)

    # ---- questions with options (AskUserQuestion)
    def qkey(self, chat, sid, tool_id, action):
        d = self.store.data
        qm = d.setdefault("qmap", {})
        ent = [chat, sid, tool_id, action]
        for n, e in qm.items():
            if e == ent:
                return n
        d["qseq"] = d.get("qseq", 0) + 1
        n = str(d["qseq"])
        qm[n] = ent
        while len(qm) > 300:
            del qm[next(iter(qm))]
        return n

    def get_question(self, chat, tool_id):
        return self.store.data.setdefault("questions", {}).get("%s:%s" % (chat, tool_id))

    def pending_question(self, sid):
        for q in reversed(list(self.store.data.get("questions", {}).values())):
            if q["sid"] == sid and not q.get("closed"):
                content = self.transcript_answer(q)
                if content is not None:
                    # forward_tick only reads the transcripts of followed sessions: a pushed question of any other
                    # session would wait for its next question to be pushed out
                    self.on_answer({"tool_id": q["tool_id"], "content": content})
                    continue
                return q
        return None

    def transcript_answer(self, q):
        """The answer to an open question if its tool_result is in the transcript, else None. Searched by bytes and
        incrementally (q["scan"] = how far the file is searched): images put megabytes after an answer."""
        path = self.transcript_path(q["sid"])
        try:
            size = os.path.getsize(path)
        except (OSError, TypeError):
            return None
        pat = ('"%s"' % q["tool_id"]).encode()  # the ask line carries it too: every hit is parsed
        pos = q.get("scan", 0)
        if pos > size:  # the file was replaced
            pos = 0
        if pos >= size:
            return None
        start = max(0, pos - len(pat))
        try:
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(size - start)
        except OSError:
            return None
        k = data.find(pat)
        while k >= 0:
            a, b = data.rfind(b"\n", 0, k) + 1, data.find(b"\n", k)
            if b < 0:  # the line is still being written
                q["scan"] = start + a
                return None
            for e in parse_transcript_events(data[a:b + 1])[0]:
                if e["kind"] == "answer" and e["tool_id"] == q["tool_id"]:
                    return e["content"]
            k = data.find(pat, b + 1)
        q["scan"] = size
        return None

    def question_shown(self, chat, sid):
        """An open question of the session already shown to this chat (message or PWA push)."""
        q = self.pending_question(sid)
        return bool(q and q.get("chat") == chat and (q.get("mid") or q.get("pushed")))

    def question_head(self, q):
        return "❓ " + q["name"]

    def question_base_lines(self, q):
        lines = [self.question_head(q)]
        for qq in q["questions"]:
            lines.append(("%s: %s" % (qq["header"], qq["question"])) if qq["header"] else qq["question"])
        return lines

    def question_view(self, q):
        """-> (plain, html, markup) for the current step."""
        qs, step = q["questions"], q["step"]
        chat, sid, tid = q["chat"], q["sid"], q["tool_id"]

        def btn(text, action):
            return {"text": text, "callback_data": "q:" + self.qkey(chat, sid, tid, action)}
        lines = [self.question_head(q)]
        if step >= len(qs):
            lines += [_t("Review the answers and submit."), ""]
            for i, qq in enumerate(qs):
                lines.append("• %s — %s" % (qq["header"] or qq["question"], short(q.get("answers", {}).get(str(i), "—"), 80)))
            rows = [[btn(_t("✅ Submit answers"), "sub:%d" % step)],
                    [btn(_t("✖ Cancel"), "esc:%d" % step)]]
        else:
            cur = qs[step]
            if len(qs) > 1:
                lines.append(_t("Question %d of %d") % (step + 1, len(qs)))
            hdr = cur["header"]
            redundant = (not hdr) or hdr.lower().rstrip("?") in cur["question"].lower()
            lines.append(cur["question"] if redundant else "%s: %s" % (hdr, cur["question"]))
            marks = q.get("marks", {}).get(str(step), [])
            rows = []
            descs = [(k, o) for k, o in enumerate(cur["options"], 1)
                     if o["description"] and o["description"].lower().strip(" .") not in (
                         o["label"].lower(), ) and o["label"].lower() not in o["description"].lower()[:len(o["label"]) + 12]]
            if descs:
                lines.append("")
            for k, o in descs:
                lines.append("%s — %s" % (o["label"], o["description"]))
            for k, o in enumerate(cur["options"], 1):
                label = short(o["label"], 30)
                if cur["multi"]:
                    label = ("☑ " if k in marks else "☐ ") + label
                rows.append([btn(label, "o:%d:%d" % (step, k))])
            if cur["multi"]:
                rows.append([btn(_t("Next →"), "next:%d" % step)])
            rows.append([btn(_t("✏️ Your own answer"), "own:%d" % step), btn(_t("✖ Cancel"), "esc:%d" % step)])
        plain = "\n".join(lines)
        html = "<b>%s</b>\n%s" % (esc(lines[0]), esc("\n".join(lines[1:])))
        return plain, html, {"inline_keyboard": rows}

    def post_question(self, chat, q):
        plain, html, markup = self.question_view(q)
        r = self._threaded(chat, q["sid"], lambda th: self.tg.send_rich(chat, html, plain, markup, thread=th))
        q["mid"] = r.get("message_id") if isinstance(r, dict) else None
        self.remember(chat, [r], q["sid"])
        self.store.save()

    def render_question(self, q, mid=None):
        if not (mid or q.get("mid")):
            return  # the question was opened from the mini-app: there is no chat message
        plain, html, markup = self.question_view(q)
        self.tg.edit(q["chat"], mid or q["mid"], plain, markup, html=html)

    def end_question_message(self, q, note, mid=None):
        if not (mid or q.get("mid")):
            return
        heads = [qq["header"] or short(qq["question"], 40) for qq in q["questions"]]
        self.tg.edit(q["chat"], mid or q["mid"], "❓ %s — %s" % (short(", ".join(heads), 60), note))

    def register_question(self, chat, sid, ev):
        """Registers a question in the store (without a chat message). -> (q, is new)."""
        key = "%s:%s" % (chat, ev["tool_id"])
        qd = self.store.data.setdefault("questions", {})
        if key in qd:
            return qd[key], False
        if self.is_old(sid) or self.is_archived(sid):
            return None, False
        qd[key] = {"chat": chat, "sid": sid, "tool_id": ev["tool_id"], "questions": ev["questions"],
                   "name": self.session_name(sid), "step": 0, "marks": {}, "answers": {},
                   "closed": False, "mid": None}
        while len(qd) > 60:
            del qd[next(iter(qd))]
        return qd[key], True

    def on_ask(self, chat, sid, ev):
        q, _ = self.register_question(chat, sid, ev)
        if q is None or q.get("closed") or q.get("mid"):
            return
        if self.webapp_recent(sid):
            return  # the owner is working with the session in the mini-app: the question is registered quietly
        if q.get("pushed"):
            return
        if self.is_watched(sid):
            qq = q["questions"][min(q["step"], len(q["questions"]) - 1)]
            if self.push_notify(chat, sid, _t("question: %s") % qq["question"], "high"):
                q["pushed"] = True  # PWA instead of Telegram: there will be no chat message
                self.store.save()
                return
        self.touch_topic(chat, sid)
        self.post_question(chat, q)

    def on_answer(self, ev):
        for q in list(self.store.data.get("questions", {}).values()):
            if q["tool_id"] == ev["tool_id"] and not q.get("closed"):
                q["closed"] = True
                if q.get("mid"):
                    try:
                        self.end_question_message(q, "✔ " + (answer_summary(ev["content"], True) or _t("received")))
                    except TGError as e:
                        log("question close failed: %s" % e)
        self.store.save()

    def sync_from_screen(self, q, pane):
        plain = strip_ansi(pane)
        n = len(q["questions"])
        if "Review your answers" in plain or "Ready to submit" in plain:
            q["step"] = n
        elif q["step"] < n and q["questions"][q["step"]]["multi"]:
            marks = parse_marks(plain)
            if marks:
                q["marks"][str(q["step"])] = sorted(k for k, v in marks.items() if v)

    def question_press(self, chat, mid, n):
        ent = self.store.data.get("qmap", {}).get(str(n))
        if not ent:
            self.popup(chat, _t("This button is outdated. Open 📺 Screen or write to the session."))
            return
        _, sid, tool_id, action = ent
        q = self.get_question(chat, tool_id)
        if not q or q.get("closed"):
            if mid:
                self.tg.edit(chat, mid, _t("The question is already closed."))
            self.popup(chat, _t("The question is already closed."))
            return
        kind, _, rest = action.partition(":")
        step = int(rest.split(":")[0])
        qs = q["questions"]
        if kind == "own":
            if step != q["step"] or step >= len(qs):
                self.render_question(q, mid)
                return
            c = self.store.chat(chat)
            c["pending_own"] = {"key": "%s:%s" % (chat, tool_id), "step": step}
            self.store.save()
            r = self.tg.send_one(chat, _t("Your answer to: ") + qs[step]["question"],
                                 {"force_reply": True, "input_field_placeholder": _t("Your answer")},
                                 thread=self._th(chat, sid))
            self.remember(chat, [r], sid)
            return
        ok, err, code = self.question_do(chat, q, kind, step, rest, mid)
        if not ok and code in ("tmux", "sdk"):
            self.popup(chat, "⚠️ " + err, sid=sid, alert=True)
        elif not ok and code in ("closed", "stale") and err:
            self.popup(chat, err)

    def sdk_answer_question(self, q, decision, mid=None):
        """Sends the collected answers of an AskUserQuestion to the runner. -> (ok, error text, code)."""
        sid = q["sid"]
        try:
            pend = [r for r in (self.sdk.get(sid).get("pending") or []) if isinstance(r, dict)
                    and r.get("tool_use_id") == q["tool_id"] and r.get("req")]
            if not pend:
                q["closed"] = True
                self.store.save()
                self.end_question_message(q, _t("The question is already closed."), mid)
                return False, _t("The question is already closed."), "closed"
            params = {"id": sid, "req": pend[0]["req"], "decision": decision}
            if decision == "allow":
                params["answers"] = {qq["question"]: q["answers"][str(i)] for i, qq in enumerate(q["questions"])
                                     if q["answers"].get(str(i)) not in (None, "—")}
            self.sdk.call("session.answer", params, timeout=10.0)
        except SdkError as e:
            return False, sdk_err_text(e), "sdk"
        q["waiting"] = True  # the closing comes from the transcript (on_answer)
        self.end_question_message(q, _t("⏳ Answer sent, waiting for the session to confirm."), mid)
        self.store.save()
        return True, None, None

    def question_do_sdk(self, q, kind, step, rest, mid=None):
        """Steps are collected locally; the runner gets one answer at the end (single choice: at once)."""
        qs = q["questions"]
        if step != q["step"]:
            self.render_question(q, mid)  # a button from the previous step
            return False, _t("The question is already on another step."), "stale"
        decision = None
        if kind == "o":
            k = int(rest.split(":")[1])
            cur = qs[step]
            if cur["multi"]:
                marks = set(q["marks"].get(str(step), []))
                marks.symmetric_difference_update({k})
                q["marks"][str(step)] = sorted(marks)
            else:
                q["answers"][str(step)] = cur["options"][k - 1]["label"]
                if len(qs) > 1:
                    q["step"] = step + 1
                else:
                    decision = "allow"
        elif kind == "next":
            cur = qs[step]
            q["answers"][str(step)] = ", ".join(
                cur["options"][k - 1]["label"] for k in q["marks"].get(str(step), [])) or "—"
            q["step"] = step + 1
        elif kind == "sub":
            decision = "allow"
        elif kind == "esc":
            decision = "deny"
        if decision:
            ok, err, code = self.sdk_answer_question(q, decision, mid)
            if not ok:
                return ok, err, code
        else:
            self.render_question(q, mid)
        self.store.save()
        return True, None, None

    def question_own_sdk(self, q, step, text):
        qs = q["questions"]
        if step != q["step"] or step >= len(qs):
            self.render_question(q)
            return False, _t("The question is already on another step."), "stale"
        q["answers"][str(step)] = text
        q["marks"].pop(str(step), None)
        if len(qs) > 1:
            q["step"] = step + 1
            self.render_question(q)
            self.store.save()
            return True, None, None
        return self.sdk_answer_question(q, "allow")

    def question_do(self, chat, q, kind, step, rest, mid=None):
        """A question button press (from chat or mini-app). -> (ok, error text, code)."""
        sid, qs = q["sid"], q["questions"]
        if is_sdk(sid):
            return self.question_do_sdk(q, kind, step, rest, mid)
        with self.tmux.lock(sid):
            ok, err = self.tmux.ensure(sid)
            if not ok:
                return False, err, "tmux"
            pane = self.tmux.capture(sid, ansi=True)
            if not menu_active(pane):
                q["closed"] = True
                self.store.save()
                self.end_question_message(q, _t("The question is already closed."), mid)
                return False, _t("The question is already closed."), "closed"
            self.sync_from_screen(q, pane)
            if step != q["step"]:
                self.render_question(q, mid)  # a button from the previous step
                return False, _t("The question is already on another step."), "stale"
            if kind == "o":
                k = int(rest.split(":")[1])
                cur = qs[step]
                self.tmux.keys(sid, str(k))
                if cur.get("preview") and not cur["multi"]:
                    self.tmux.keys(sid, "Enter")
                label = cur["options"][k - 1]["label"]
                if cur["multi"]:
                    marks = set(q["marks"].get(str(step), []))
                    marks.symmetric_difference_update({k})
                    q["marks"][str(step)] = sorted(marks)
                else:
                    q["answers"][str(step)] = label
                    if len(qs) > 1:
                        q["step"] = step + 1
            elif kind == "next":
                self.tmux.keys(sid, "Right")
                cur = qs[step]
                q["answers"][str(step)] = ", ".join(
                    cur["options"][k - 1]["label"] for k in q["marks"].get(str(step), [])) or "—"
                q["step"] = step + 1
            elif kind == "sub":
                self.tmux.keys(sid, "1")
            elif kind == "esc":
                self.tmux.keys(sid, "Escape")
            self.after_question_key(q, sid, mid)
        return True, None, None

    def after_question_key(self, q, sid, mid):
        self.sleep(1.0)
        pane = self.tmux.capture(sid, ansi=True)
        if not menu_active(pane):
            q["waiting"] = True  # the menu is gone: waiting for the tool_result in the transcript
            self.end_question_message(q, _t("⏳ Answer sent, waiting for the session to confirm."), mid)
        else:
            q.pop("waiting", None)
            self.sync_from_screen(q, pane)
            self.render_question(q, mid)
        self.store.save()

    def question_own(self, chat, key, step, text, user_mid=None):
        q = self.store.data.get("questions", {}).get(key)
        if not q or q.get("closed"):
            self.say_temp(chat, _t("The question is already closed, the answer was not sent."))
            return
        sid = q["sid"]
        try:
            ok, msg, code = self.question_own_do(q, step, text)
            if not ok:
                if code == "closed":
                    self.say_temp(chat, _t("The question is already closed, the answer was not sent."), sid=sid)
                else:
                    self.say_temp(chat, "⚠️ " + msg, sid=sid, reply_to=user_mid)
                return
            try:
                if not user_mid:
                    raise TGError("no message")
                self.tg.react(chat, user_mid)
            except TGError:
                self.say(chat, "📨 → " + self.session_name(sid), sid=sid)
        except TGError as e:
            log("question_own tg error: %s" % e)

    def question_own_do(self, q, step, text):
        """"Your own answer": digit N+1, paste without clearing the field, check, Enter. -> (ok, text, code)."""
        sid, qs = q["sid"], q["questions"]
        text = clean_paste(text)
        if not text.strip():
            return False, _t("Empty answer."), "paste"
        if is_sdk(sid):
            return self.question_own_sdk(q, step, text.strip())
        with self.tmux.lock(sid):
            ok, err = self.tmux.ensure(sid)
            if not ok:
                return False, err, "tmux"
            if not menu_active(self.tmux.capture(sid, ansi=True)):
                q["closed"] = True
                self.store.save()
                return False, _t("The question is already closed."), "closed"
            chat = qs[step].get("preview") and not qs[step]["multi"]
            if chat:
                # previews: no "Type something" row, digits and pasted text are ignored; «Chat about this» (last row,
                # the cursor neither wraps nor passes it) ends the question and frees the prompt for the text
                self.tmux.keys(sid, *["Down"] * (len(qs[step]["options"]) + 1))
                self.sleep(0.5)
                self.tmux.keys(sid, "Enter")
                for _ in range(20):
                    if not menu_active(self.tmux.capture(sid, ansi=True)):
                        break
                    self.sleep(0.25)
                else:
                    return False, _t("The question did not close. Open 📺 Screen."), "unconfirmed"
                q["closed"] = True
            else:
                self.tmux.keys(sid, str(len(qs[step]["options"]) + 1))
                self.sleep(0.8)
            ok, err = self.tmux.paste_text(sid, text)
            if not ok:
                return False, err, "paste"
            pre = first_line_prefix(text)
            for _ in range(20):
                if pre and pre in collapse(strip_ansi(self.tmux.capture(sid, ansi=True))):
                    break
                self.sleep(0.25)
            else:
                return False, (_t("Not sure the text reached the answer, so Enter was not pressed. Open 📺 "
                                  "Screen.")), "unconfirmed"
            self.sleep(0.5)
            self.tmux.keys(sid, "Enter")
            if not qs[step]["multi"]:
                q["answers"][str(step)] = short(text, 60)
                if len(qs) > 1:
                    q["step"] = step + 1
            self.after_question_key(q, sid, q.get("mid"))
        return True, None, None

    # ---- mini-app (Telegram Web App): data from the cache, claude agents is not called on requests
    def webapp_url(self):
        if self.cfg.webapp_url_env:
            return self.cfg.webapp_url_env
        try:
            with open(self.cfg.tunnel_file, encoding="utf-8") as f:
                u = f.read().strip()
            return u if u.startswith("https://") else None
        except OSError:
            return None

    def webapp_tick(self):
        """Watches the tunnel address: the bot menu button and the panel button."""
        url = self.webapp_url()
        if self.webapp_menu_initialized and url == self.webapp_url_cur:
            return
        menu_button = ({"type": "web_app", "text": _t("Sessions"),
                        "web_app": {"url": url.rstrip("/") + "/app/"}}
                       if url else {"type": "commands"})
        for uid in self.cfg.allowed:
            try:
                self.tg.call("setChatMenuButton", {"chat_id": uid, "menu_button": menu_button})
            except TGError as e:
                log("setChatMenuButton failed: %s" % e)
                return  # will retry on the next tick
        self.webapp_url_cur = url
        self.webapp_menu_initialized = True
        for uid in self.cfg.allowed:
            try:
                self.panel_publish(uid)
            except TGError as e:
                log("panel publish failed: %s" % e)

    def api_quota(self):
        d = read_quota(self.cfg.rate_file)
        now = self.clock()
        d = d or {}

        def pct(x):
            v = x.get("used_percentage") if isinstance(x, dict) else None
            return float(v) if isinstance(v, (int, float)) else None
        five = d.get("five_hour")
        rs = five.get("resets_at") if isinstance(five, dict) else 0
        upd = d.get("updated_at")
        upd = upd if isinstance(upd, (int, float)) and upd > 0 else None
        return {"h5": pct(five), "d7": pct(d.get("seven_day")),
                "stale": bool(upd and now - upd > STALE_QUOTA), "updated": upd,
                "h5_reset": datetime.fromtimestamp(rs).strftime("%H:%M") if isinstance(rs, (int, float)) and rs else None}

    def api_row(self, uid, sid, now):
        info = self.session_info(sid)
        old = self.is_old(sid)
        archived = self.is_archived(sid)
        parts = (self.ask_parts(info) if not old and not archived and info["state"] in ("working", "blocked")
                 else None)
        ask = (parts[0]["question"], [o["label"] for o in parts[0]["options"]]) if parts else None
        state = info["state"]
        if archived:
            group = "archived"
        elif old:
            group = "old"
        elif state == "blocked" or ask:
            group = "wait"
        elif state in ("done", "failed"):
            group = "done"
        else:
            group = "work"
        k = self.chain_k(sid)
        kid = self.chain_child(sid) if old else None
        detail = (_t("question: %s") % ask[0]) if ask else humanize_detail(info["detail"] or info["result"] or "")
        perm = False if (old or archived) else self.perm_flag(sid, state)
        return {"id": sid, "name": info["name"], "base_name": info.get("base_name") or info["name"], "k": k,
                "group": group, "state": state, "failed": state == "failed", "detail": detail,
                "ago": ago_short(info["updated"], now), "updated": info["updated"],
                "kind": info.get("kind") or "claude", "source": info.get("source") or "claude",
                "herdr": is_herdr(sid), "sdk": is_sdk(sid),
                "perm": perm, "perm_title": self.perm_title(sid) if perm else "",
                "perm_body": (short(self.perm_body(sid), 200) or None) if perm else None,
                "wait_question": {"question": short(parts[0]["question"], 240), "multi": bool(parts[0]["multi"]),
                                  "n": parts[2], "step": parts[1],
                                  "options": [{"label": o["label"], "description": o["description"]}
                                              for o in parts[0]["options"][:4]]} if parts else None,
                "topic": sid in self.store.chat(uid)["topics"], "watched": self.is_watched(sid),
                "current": (not old) and k >= 2, "archived_at": self.archived_at(sid),
                "into": {"id": kid, "name": self.display_name(kid)} if kid else None,
                "ask": {"question": ask[0], "options": ask[1]} if ask else None}

    def api_ids(self):
        ids = self.last_full if self.last_full is not None else set(self.known)
        return [sid for sid in list(self.known) if sid in ids]

    def api_sessions(self, uid):
        now = self.clock()
        rows = [self.api_row(uid, sid, now) for sid in self.api_ids()]
        rows.sort(key=lambda r: -r["updated"])
        return {"quota": self.api_quota(), "topics": bool(self.topics_enabled), "lang": LANG,
                "voice": self.stt_ready(), "sessions": rows}

    def api_history(self, sid):
        path = self.transcript_path(sid)
        try:
            st = os.stat(path)
            key = (st.st_size, st.st_mtime)
        except (OSError, TypeError):
            return []
        cached = self._hist.get(sid)
        if cached and cached[0] == key:
            return cached[1]
        # the window grows until the conversation is collected: the log tail may be full of images (lines of hundreds of KB)
        tail, hist = HISTORY_TAIL, []
        while True:
            try:
                with open(path, "rb") as f:
                    start = max(0, st.st_size - tail)
                    f.seek(start)
                    data = f.read()
            except OSError:
                return []
            if start > 0:
                nl = data.find(b"\n")
                data = data[nl + 1:] if nl >= 0 else b""
            end = data.rfind(b"\n")
            hist = parse_history(data[:end] if end >= 0 else b"", home=self.cfg.home)
            if start == 0 or tail >= HISTORY_TAIL_MAX or len(hist) >= HISTORY_LIMIT:
                break
            tail *= 2
        self._hist[sid] = (key, hist)
        return hist

    def api_question_obj(self, uid, sid):
        """A session's pending question (registered in the store without a chat message) or None."""
        q = self.pending_question(sid)
        # an answered question waits for its tool_result; a newer ask in the transcript means it was answered
        if (q is None or q.get("waiting")) and not self.is_old(sid):
            path = self.transcript_path(sid)
            try:
                st = os.stat(path)
                key = (st.st_size, st.st_mtime)
            except (OSError, TypeError):
                return None
            cached = self._ask_cache.get(sid)
            if cached and cached[0] == key:
                ev = cached[1]
            else:
                ev = unanswered_ask(path, tail_bytes=256 * 1024)
                self._ask_cache[sid] = (key, ev)
            if ev and (q is None or ev["tool_id"] != q["tool_id"]):
                if q is not None:
                    q["closed"] = True
                q, _ = self.register_question(uid, sid, ev)
        return q

    @staticmethod
    def api_question_view(q):
        if not q or q.get("closed") or q.get("waiting"):
            return None
        qs, step = q["questions"], q["step"]
        n = len(qs)
        review = step >= n
        cur = qs[min(step, n - 1)]
        marks = [] if review else q.get("marks", {}).get(str(step), [])
        return {"tool_id": q["tool_id"], "step": step, "n": n, "header": cur["header"],
                "question": cur["question"], "multi": bool(cur["multi"]), "review": review,
                "options": [] if review else [{"label": o["label"], "description": o["description"],
                                               "marked": (k in marks)}
                                              for k, o in enumerate(cur["options"], 1)],
                "answers": [{"header": qq["header"], "question": qq["question"],
                             "answer": q.get("answers", {}).get(str(i))} for i, qq in enumerate(qs)]}

    def api_session(self, uid, sid):
        now = self.clock()
        row = self.api_row(uid, sid, now)
        info = self.session_info(sid)
        parent, kid = self.chain_parent(sid), self.chain_child(sid)
        row.update({"cwd": tilde(info["cwd"]), "result": info["result"],
                    "prev": {"id": parent, "name": self.display_name(parent)} if parent else None,
                    "next": {"id": kid, "name": self.display_name(kid)} if kid else None,
                    "history": self.api_history(sid),
                    "subagents_count": self.subagents_count(sid), "lang": LANG,
                    "question": self.api_question_view(self.api_question_obj(uid, sid)),
                    "perm": None if (self.is_old(sid) or self.is_archived(sid)) else self.perm_view(sid, info["state"])})
        return row

    # ---- session subagents: read-only
    def subagents_dir(self, sid):
        path = self.transcript_path(sid)
        if not isinstance(path, str) or not path.endswith(".jsonl"):
            return None
        d = path[:-len(".jsonl")] + "/subagents"
        return d if os.path.isdir(d) else None

    def subagents_scan(self, sid):
        """-> [record] by freshness (up to 50). Cached by folder/file mtime and size; the status is computed on the fly."""
        d = self.subagents_dir(sid)
        if not d:
            return []
        parent = self.transcript_path(sid)
        try:
            names = sorted(n for n in os.listdir(d) if n.startswith("agent-") and n.endswith(".jsonl"))
            stats = {}
            for n in names:
                st = os.stat(os.path.join(d, n))
                try:
                    mt = os.stat(os.path.join(d, n[:-6] + ".meta.json")).st_mtime
                except OSError:
                    mt = 0
                stats[n] = (st.st_size, st.st_mtime, mt)
            pst = os.stat(parent)
            key = (os.stat(d).st_mtime, tuple(sorted(stats.items())), pst.st_size, pst.st_mtime)
        except OSError:
            return []
        cached = self._sub_cache.get(sid)
        if cached and cached[0] == key:
            return cached[1]
        recs = []
        root = os.path.realpath(d)
        for n in sorted(names, key=lambda n: -stats[n][1])[:SUB_MAX]:
            aid = n[len("agent-"):-len(".jsonl")]
            if not SUB_ID_RE.match(aid) or not os.path.realpath(os.path.join(d, n)).startswith(root + os.sep):
                continue  # only files in this session's folder (a symlink pointing outside is not read)
            try:
                with open(os.path.join(d, n[:-6] + ".meta.json"), encoding="utf-8") as f:
                    meta = json.load(f)
                meta = meta if isinstance(meta, dict) else {}
            except (OSError, ValueError):
                meta = {}
            try:
                rec = scan_subagent_file(os.path.join(d, n))
            except OSError:
                continue
            rec.update({"id": aid, "type": meta.get("agentType") or "", "description": meta.get("description") or "",
                        "model": meta.get("model") or "", "tool_use_id": meta.get("toolUseId") or "",
                        "background": meta.get("requestShape") == "background"})
            recs.append(rec)
        notes, results = scan_parent_completions(parent, {r["id"] for r in recs},
                                                 {r["tool_use_id"] for r in recs if r["tool_use_id"]})
        for r in recs:
            r["completions"] = subagent_completions(notes.get(r["id"], []), results, r["tool_use_id"], r["background"])
        self._sub_cache[sid] = (key, recs)
        return recs

    def api_subagents(self, sid):
        now = self.clock()
        items = []
        for r in self.subagents_scan(sid):
            items.append({"id": r["id"], "type": r["type"], "description": r["description"], "model": r["model"],
                          "background": r["background"], "started": r["started"], "updated": r["updated"],
                          "status": subagent_status(r, now), "runs": len(r["completions"]), "last": r["last"],
                          "tools": r["tools"]})
        return {"subagents": items}

    def subagents_count(self, sid):
        now = self.clock()
        recs = self.subagents_scan(sid)
        return {"working": sum(1 for r in recs if subagent_status(r, now) == "working"), "total": len(recs)}

    def api_subagent(self, sid, agent_id):
        if not SUB_ID_RE.match(agent_id or ""):
            return 400, {"error": "bad agent id"}
        d = self.subagents_dir(sid)
        if not d:
            return 404, {"error": "no such subagent"}
        root = os.path.realpath(d)
        path = os.path.realpath(os.path.join(d, "agent-%s.jsonl" % agent_id))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):  # only files in this session's folder
            return 404, {"error": "no such subagent"}
        rec = next((r for r in self.subagents_scan(sid) if r["id"] == agent_id), None)
        if rec is None:
            return 404, {"error": "no such subagent"}
        try:
            data = read_tail_lines(path)
            with open(path, "rb") as f:
                first = f.readline(SUB_TAIL)
        except OSError:
            return 404, {"error": "no such subagent"}
        task, task_obj = "", {}
        try:
            task_obj = json.loads(first)
            content = (task_obj.get("message") or {}).get("content")
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
            task = content.strip() if isinstance(content, str) else ""
        except (ValueError, AttributeError):
            pass
        hist = parse_history(data, limit=40, home=self.cfg.home, sidechain=True)
        if task:
            hist = [h for h in hist if not (h["role"] == "user" and h.get("text") == task[:4000])]
            hist.insert(0, {"role": "task", "text": task[:4000], "uuid": task_obj.get("uuid") or "",
                            "ts": task_obj.get("timestamp")})
        return 200, {"id": agent_id, "type": rec["type"], "description": rec["description"], "model": rec["model"],
                     "background": rec["background"], "status": subagent_status(rec, self.clock()),
                     "started": rec["started"], "updated": rec["updated"], "history": hist}

    def api_send(self, uid, sid, text):
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": _t("Empty message"), "code": "empty"}
        if text.startswith("/") and "\n" not in text and text.split()[0] in command_names():
            with self.api_lock:
                return self.api_command(uid, sid, text)[1]
        self.webapp_touch[sid] = self.clock()
        ok, err = self.deliver(uid, sid, text, origin="webapp")
        log("webapp send %s: %s" % (sid, "ok" if ok else ("menu" if err == ERR_MENU else "error: " + short(str(err), 90))))
        if ok:
            return {"ok": True, "error": None, "code": None}
        return {"ok": False, "error": (_t("The session is waiting for an answer to a question") if err == ERR_MENU else err),
                "code": "menu" if err == ERR_MENU else "error"}

    def api_upload(self, uid, sid, body):
        """A file from the mini-app: multipart (file, caption) -> ~/tg-inbox + deliver(origin=webapp)."""
        try:
            parsed = parse_multipart(body.get("content_type", ""), body.get("raw", b""))
        except Exception:
            parsed = None
        if not parsed or not parsed[1]:
            return 400, {"ok": False, "error": _t("No file in the request")}
        fields, _first = parsed
        files = fields.pop("_files", None) or [_first]
        if len(files) > UPLOAD_FILES_MAX:
            return 413, {"ok": False, "error": _t("No more than %d files at a time") % UPLOAD_FILES_MAX}
        if any(len(d) > FILE_MAX for _, d in files):
            return 413, {"ok": False, "error": _t("File is larger than 50 MB")}
        self.webapp_touch[sid] = self.clock()
        saved = []
        folder = self.inbox_folder(sid)
        try:
            for fname, data in files:
                saved.append((save_inbox(self.cfg.home, data, fname, self.clock(), folder), len(data), mimetypes.guess_type(fname)[0]))
        except OSError:
            return 500, {"ok": False, "error": _t("Could not save the file")}
        cap = fields.get("caption", "")
        if len(saved) == 1:
            text = file_message(saved[0][0], saved[0][1], saved[0][2], cap)
        else:  # several files in one message so the session receives them together
            text = _t("Files from owner (%d):\n") % len(saved) + "\n".join(
                "• %s (%s, %s)" % (pth, human_size(n), m or "application/octet-stream") for pth, n, m in saved)
            if cap.strip():
                text += "\n" + cap.strip()
        ok, err = self.deliver(uid, sid, text, origin="webapp")
        log("webapp upload %s: %d file(s), %s" % (sid, len(saved), "ok" if ok else ("menu" if err == ERR_MENU else "error: " + short(str(err), 90))))
        paths = [x[0] for x in saved]
        if not ok:
            return 200, {"ok": False, "path": paths[0], "paths": paths, "size": saved[0][1],
                         "error": _t("The session is waiting for an answer to a question") if err == ERR_MENU else err,
                         "code": "menu" if err == ERR_MENU else "error"}
        return 200, {"ok": True, "path": paths[0], "paths": paths, "size": saved[0][1]}

    def api_voice(self, uid, body):
        """POST /api/voice: the body is one audio recording (Content-Type audio/*, up to VOICE_MAX).
        Not under api_lock: recognition may take long. -> (HTTP code, object)."""
        ctype = str(body.get("content_type") or "").split(";")[0].strip().lower()
        raw = body.get("raw") or b""
        if not ctype.startswith("audio/"):
            return 415, {"ok": False, "code": "error", "error": _t("Unsupported audio type")}
        if not raw:
            return 400, {"ok": False, "code": "error", "error": _t("No audio in the request")}
        if len(raw) > VOICE_MAX:
            return 413, {"ok": False, "code": "too_large", "error": _t("Voice message is larger than 10 MB")}
        sub = ctype.split("/", 1)[1].split("+")[0]
        ext = {"ogg": ".ogg", "webm": ".webm", "mp4": ".mp4", "mpeg": ".mp3", "mp3": ".mp3", "wav": ".wav",
               "x-wav": ".wav", "aac": ".aac", "x-m4a": ".m4a"}.get(sub, ".bin")
        code, res = self.stt_bytes(raw, ext)
        if code != "ok":
            return 200, {"ok": False, "code": code, "error": self.stt_problem(code)}
        try:
            seconds = float(res.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        return 200, {"ok": True, "text": str(res["text"]).strip(), "seconds": seconds,
                     "engine": str(res.get("engine") or "")}

    def api_file(self, uid, raw_path):
        ok = file_ok(raw_path, self.cfg.home)
        if not ok:
            return 403, {"error": "forbidden"}
        return 200, {"url": "/f/" + make_file_token(ok[0], self.cfg.token, self.clock())}

    def file_for_token(self, token):
        path = parse_file_token(token, self.cfg.token, self.clock())
        if not path or not file_ok(path, self.cfg.home):  # the filter is checked again when serving
            return None
        return path

    def api_sendfile(self, uid, sid, raw_path):
        ok = file_ok(raw_path, self.cfg.home)
        if not ok:
            return 403, {"ok": False, "error": _t("Path is not allowed or the file does not exist")}
        try:
            with open(ok[0], "rb") as f:
                data = f.read()
            self._threaded(uid, sid, lambda th: self.tg.send_media(
                uid, os.path.basename(ok[0]), data, "📎 %s" % self.display_name(sid), thread=th))
        except OSError:
            return 404, {"ok": False, "error": _t("Could not read the file")}
        except TGError as e:
            log("sendfile failed: %s" % e)
            return 200, {"ok": False, "error": _t("Telegram did not accept the file")}
        return 200, {"ok": True}

    def process_outbox_request(self, req):
        """A cc-send request -> sendDocument into the session topic (or the main chat)."""
        sid = str(req.get("sid") or "")
        caption = str(req.get("caption") or "").strip()
        ok = file_ok(req.get("path"), self.cfg.home)
        known = sid in self.known
        name = self.display_name(sid) if known else (sid or _t("session"))
        for uid in sorted(self.cfg.allowed):
            try:
                if not ok:
                    self.say(uid, _t("Could not send the file: the path is not allowed or the file does not "
                                     "exist"),
                             sid=sid if known else None)
                    continue
                with open(ok[0], "rb") as f:
                    data = f.read()
                cap = ("📎 %s: %s" % (name, caption) if caption else "📎 %s" % name)[:1000]
                self._threaded(uid, sid if known else None, lambda th: self.tg.send_media(
                    uid, os.path.basename(ok[0]), data, cap, thread=th))
            except (TGError, OSError) as e:
                log("outbox send failed: %s" % type(e).__name__)
                try:
                    self.say(uid, _t("Could not send the file: %s") % (_t("Telegram error") if isinstance(e, TGError)
                                                                 else _t("file is unreadable")),
                             sid=sid if known else None)
                except TGError:
                    pass

    def inbox_cleanup(self, max_age=30 * 24 * 3600):
        """Files in ~/tg-inbox older than 30 days are removed, as are empty folders. -> number of removed files."""
        base = os.path.join(self.cfg.home, "tg-inbox")
        cutoff = self.clock() - max_age
        removed = 0
        for root, dirs, files in os.walk(base, topdown=False, followlinks=False):
            for n in files:
                fp = os.path.join(root, n)
                try:
                    if os.path.islink(fp):
                        if not os.path.exists(fp):  # the moved file it pointed to is gone
                            os.unlink(fp)
                    elif os.stat(fp).st_mtime < cutoff:
                        os.unlink(fp)
                        removed += 1
                except OSError:
                    pass
            if root != base:
                try:
                    os.rmdir(root)  # empty directories only
                except OSError:
                    pass
        return removed

    def inbox_tick(self):
        now = self.clock()
        if now - self.store.data.get("inbox_cleanup_ts", 0) < 24 * 3600:
            return
        self.store.data["inbox_cleanup_ts"] = now
        self.store.save()
        n = self.inbox_cleanup()
        if n:
            log("tg-inbox: files removed %d" % n)

    def outbox_tick(self):
        d = self.cfg.outbox_dir
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
        except OSError:
            return
        for n in names[:20]:
            fp = os.path.join(d, n)
            try:
                with open(fp, encoding="utf-8") as f:
                    req = json.load(f)
            except (OSError, ValueError):
                req = None
            try:
                if isinstance(req, dict):
                    self.process_outbox_request(req)
                else:
                    log("outbox: broken request %s" % n)
            finally:
                try:
                    os.unlink(fp)
                except OSError:
                    pass

    # ---- PWA sign-in: a one-time link -> device token
    def new_login_token(self, uid):
        token = secrets.token_urlsafe(32)
        logins = self.store.data.setdefault("logins", {})
        now = self.clock()
        for h in [h for h, r in logins.items() if r["exp"] < now]:
            logins.pop(h, None)
        logins[sha256_hex(token)] = {"uid": uid, "exp": now + LOGIN_TTL}
        self.store.save()
        return token

    @staticmethod
    def device_label(ua):
        for key, label in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
                           ("Windows", "Windows"), ("Linux", "Linux")):
            if key in (ua or ""):
                return label
        return _t("Device")

    def api_login(self, ip, token, ua=""):
        now = self.clock()
        tries = [t for t in self.login_attempts.get(ip, []) if now - t < 60]
        if len(tries) >= LOGIN_ATTEMPTS:
            self.login_attempts[ip] = tries
            return 429, {"error": "rate"}
        tries.append(now)
        self.login_attempts[ip] = tries
        rec = self.store.data.setdefault("logins", {}).pop(sha256_hex(token or ""), None)  # single use
        if not rec or rec["exp"] < now or rec["uid"] not in self.cfg.allowed:
            self.store.save()
            return 401, {"error": "login"}
        device = secrets.token_urlsafe(32)
        ua = (ua or "")[:120]
        self.store.data.setdefault("devices", {})[sha256_hex(device)] = {
            "uid": rec["uid"], "created": now, "last_seen": now, "ua": ua, "label": self.device_label(ua)}
        self.store.save()
        return 200, {"ok": True, "device_token": device}

    def device_auth(self, token):
        """X-Device-Token -> (uid, device hash) or None. Sliding expiry, 90 days."""
        h = sha256_hex(token or "")
        dev = self.store.data.get("devices", {}).get(h)
        if not dev or dev["uid"] not in self.cfg.allowed:
            return None
        now = self.clock()
        if now - dev["last_seen"] > DEVICE_TTL or now - dev.get("created", now) > DEVICE_MAX_AGE:
            self.remove_device(h)
            return None
        if now - dev["last_seen"] > DEVICE_TOUCH:
            dev["last_seen"] = now
            self.store.save()
        return dev["uid"], h

    def remove_device(self, h):
        self.store.data.get("devices", {}).pop(h, None)
        push = self.store.data.get("push", {})
        for ep in [ep for ep, r in push.items() if r.get("device_hash") == h]:
            push.pop(ep, None)
        self.store.save()

    # ---- Web Push
    def vapid_key(self):
        if self._vapid is None:
            self._vapid = load_vapid_key(self.cfg.vapid_file)
        return self._vapid

    def push_enabled(self):
        return HAVE_CRYPTO and bool(self.cfg.vapid_sub)

    def api_push_key(self):
        if not self.push_enabled():
            return 501, {"error": "push unavailable"}
        return 200, {"key": vapid_public_key(self.vapid_key())}

    def api_push_subscribe(self, uid, device, body):
        if not device:
            return 403, {"error": "device"}
        ep = body.get("endpoint")
        keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
        try:
            ua_pub, auth = b64u_dec(str(keys.get("p256dh"))), b64u_dec(str(keys.get("auth")))
        except (ValueError, TypeError):
            ua_pub, auth = b"", b""
        if (not isinstance(ep, str) or not push_endpoint_ok(ep) or len(ep) > 1000
                or len(ua_pub) != 65 or ua_pub[:1] != b"\x04" or len(auth) < 8):
            return 400, {"error": "bad subscription"}
        push = self.store.data.setdefault("push", {})
        for old_ep in [e for e, r in push.items() if r.get("device_hash") == device and e != ep]:
            push.pop(old_ep, None)  # one device — one subscription
        push[ep] = {"uid": uid, "device_hash": device, "p256dh": keys["p256dh"], "auth": keys["auth"],
                    "created": self.clock(), "fails": 0}
        self.store.save()
        return 200, {"ok": True}

    def api_push_unsubscribe(self, uid, body):
        rec = self.store.data.get("push", {}).get(body.get("endpoint"))
        if rec and rec["uid"] == uid:
            self.store.data["push"].pop(body["endpoint"], None)
            self.store.save()
        return 200, {"ok": True}

    def push_post(self, endpoint, headers, data):
        """HTTP POST to a push service -> response code (0 — network unavailable). Bodies are not logged."""
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except (urllib.error.URLError, OSError):
            return 0

    def push_send_one(self, endpoint, rec, payload, urgency):
        if not push_endpoint_ok(endpoint):  # a subscription with a foreign host (an old record) is not touched
            self.store.data.get("push", {}).pop(endpoint, None)
            return False
        parts = urllib.parse.urlsplit(endpoint)
        key = self.vapid_key()
        jwt = vapid_jwt(key, "%s://%s" % (parts.scheme, parts.netloc), self.clock(), self.cfg.vapid_sub)
        headers = {"Authorization": "vapid t=%s, k=%s" % (jwt, vapid_public_key(key)), "TTL": "3600",
                   "Urgency": urgency, "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream"}
        code = self.push_post(endpoint, headers, webpush_encrypt(payload, rec["p256dh"], rec["auth"]))
        push = self.store.data.get("push", {})
        if code in (200, 201, 202):
            rec["fails"] = 0
            return True
        if code in (404, 410):
            push.pop(endpoint, None)
        else:
            rec["fails"] = rec.get("fails", 0) + 1
            if rec["fails"] >= PUSH_MAX_FAILS:
                push.pop(endpoint, None)
        log("push: response %s" % code)
        return False

    def push_notify(self, uid, sid, body, urgency="high", title=None):
        """PWA instead of Telegram: True if the push reached at least one healthy subscription."""
        if not self.push_enabled():
            return False
        subs = [(ep, r) for ep, r in list(self.store.data.get("push", {}).items()) if r.get("uid") == uid]
        if not any(r.get("fails", 0) == 0 for _, r in subs):
            return False
        payload = json.dumps({"title": short(title or self.display_name(sid), 60), "body": short(body, 120), "tag": sid,
                              "data": {"url": "/app/#s=%s" % sid}}, ensure_ascii=False).encode()
        if len(payload) > PUSH_PAYLOAD_MAX:
            return False
        delivered = False
        for ep, rec in subs:
            ok = self.push_send_one(ep, rec, payload, urgency)
            if ok and rec.get("fails", 0) == 0:
                delivered = True
        self.store.save()
        return delivered

    # ---- Telegram: /pwa and /devices
    def cmd_pwa(self, chat):
        url = (self.webapp_url() or "").rstrip("/")
        if not url:
            self.say_temp(chat, _t("PWA is not configured yet."))
            return
        code = self.new_login_token(chat)
        link = "%s/app/#login=%s" % (url, code)
        # On iPhone a link from Telegram opens in Safari, while the installed app has its own storage —
        # so sign-in is by code: copy it and paste it on the app's sign-in screen.
        text = (_t("Sign in to the «Sessions» app (%s/app/).\n"
                   "\n"
                   "iPhone: open the address in Safari → «Share» → «Add to Home Screen». "
                   "Launch the app from the home screen and paste the code:\n"
                   "\n"
                   "%s\n"
                   "\n"
                   "Android or desktop: just press the button below.\n"
                   "The code is single-use and valid for 10 minutes.") % (url, code))
        self.say_temp(chat, text, {"inline_keyboard": [[{"text": _t("Open and sign in"), "url": link}]]}, ttl=LOGIN_TTL)

    def devices_view(self, uid):
        now = self.clock()
        devs = sorted(((h, d) for h, d in self.store.data.get("devices", {}).items() if d["uid"] == uid),
                      key=lambda t: -t[1]["last_seen"])
        if not devs:
            return _t("No devices. Add one: /pwa"), {"inline_keyboard": []}
        lines = [_t("App devices:")]
        rows = []
        for h, d in devs:
            lines.append(_t("• %s · created %s · seen %s") % (d.get("label") or _t("Device"),
                                                         datetime.fromtimestamp(d["created"]).strftime("%d.%m"),
                                                         ago(d["last_seen"], now) or "—"))
            rows.append([{"text": _t("Disconnect: %s") % short(d.get("label") or _t("Device"), 20),
                          "callback_data": "dvx:" + h[:16]}])
        rows.append([{"text": _t("Sign out everywhere"), "callback_data": "dvall"}])
        return "\n".join(lines), {"inline_keyboard": rows}

    def cmd_devices(self, chat):
        text, markup = self.devices_view(chat)
        self.single(chat, "devices", text, markup or None)

    def api_answer(self, uid, sid, body):
        self.webapp_touch[sid] = self.clock()
        q = self.api_question_obj(uid, sid)
        if q is None or q.get("closed"):
            return {"ok": False, "error": _t("No question, or it is already closed"), "question": None}
        action = body.get("action")
        step = q["step"]
        ok, err = True, None
        if action == "own":
            text = str(body.get("text") or "").strip()
            if not text or step >= len(q["questions"]):
                return {"ok": False, "error": _t("Empty answer"), "question": self.api_question_view(q)}
            ok, err, _ = self.question_own_do(q, step, text)
        elif action == "option":
            try:
                k = int(body.get("index"))
            except (TypeError, ValueError):
                k = 0
            if step >= len(q["questions"]) or not 1 <= k <= len(q["questions"][step]["options"]):
                return {"ok": False, "error": _t("No such option"), "question": self.api_question_view(q)}
            ok, err, _ = self.question_do(uid, q, "o", step, "%d:%d" % (step, k))
        elif action in ("next", "submit", "cancel"):
            kind = {"next": "next", "submit": "sub", "cancel": "esc"}[action]
            ok, err, _ = self.question_do(uid, q, kind, step, str(step))
        else:
            return {"ok": False, "error": _t("Unknown action"), "question": self.api_question_view(q)}
        self.store.save()
        return {"ok": bool(ok), "error": err, "question": self.api_question_view(q)}

    def is_live(self, sid):
        k = self.known.get(sid) or {}
        if k.get("source") == "sdk":
            return k.get("sdk_state") != "stopped"
        return bool(k.get("pid")) or k.get("source") == "herdr"

    def api_screen(self, sid):
        if is_sdk(sid):
            return {"menu": False, "screen": None, "stopped": not self.is_live(sid)}
        if not self.is_live(sid):
            return {"menu": False, "screen": None, "stopped": True}
        if is_herdr(sid):
            ok, text = self.herdr_screen(sid)
            if not ok:
                return {"menu": False, "screen": None, "error": text}
            return {"menu": False, "screen": trim_screen(text, 30, 6000)}
        with self.tmux.lock(sid):
            rc, _ = self.tmux.run(["tmux", "has-session", "-t", "=" + self.tmux.name(sid)])
            if rc != 0:  # no window yet — open one (waits for the input line); this is acceptable for a live session
                ok, err = self.tmux.ensure(sid)
                if not ok:
                    return {"menu": False, "screen": None, "error": err}
            now = self.clock()
            if now - self._rewidth_at.get(sid, 0) >= REWIDTH_EVERY:  # throttled: the screen comes with every key press
                self._rewidth_at[sid] = now
                self.tmux.rewidth(sid)
            pane = self.tmux.capture(sid)
        return {"menu": dialog_active(pane), "screen": trim_screen(pane, 30, 6000)}

    # ---- permission dialogs (tmux screen / SDK requests)
    def perm_pending(self, sid):
        """Pending permission requests of an SDK session (kept in `known` by the runner events)."""
        k = self.known.get(sid) or {}
        if k.get("sdk_state") == "stopped":
            return []
        return [r for r in (k.get("pending") or []) if isinstance(r, dict) and r.get("kind") == "perm" and r.get("req")]

    def perm_flag(self, sid, state):
        """Does the session wait for a permission now? Never reads a screen: tmux comes from the tick's cache."""
        if is_sdk(sid):
            return bool(self.perm_pending(sid))
        return state == "blocked" and sid in self._perm_cache

    def perm_title(self, sid):
        """Tool name (SDK) or dialog title (tmux, e.g. «Bash command») of the open request, from the cache."""
        if is_sdk(sid):
            pend = self.perm_pending(sid)
            return str(pend[0].get("tool") or "") if pend else ""
        ent = self._perm_cache.get(sid)
        return ent["perm"]["title"] if ent else ""

    def perm_body(self, sid):
        """The command / path of the open permission request (cache only)."""
        if is_sdk(sid):
            pend = self.perm_pending(sid)
            if not pend:
                return ""
            head, body = sdk_input_parts(pend[0].get("tool"), pend[0].get("input"))
            return body or head or ""
        ent = self._perm_cache.get(sid)
        return (ent["perm"]["body"] or ent["perm"]["summary"] or "") if ent else ""

    def perm_line(self, sid):
        """«<title>: <short command>» of the open permission request (cache only, no screen is read)."""
        if is_sdk(sid):
            pend = self.perm_pending(sid)
            if not pend:
                return ""
            head, body = sdk_input_parts(pend[0].get("tool"), pend[0].get("input"))
            return perm_brief(str(pend[0].get("tool") or "?"), body or head)
        ent = self._perm_cache.get(sid)
        if not ent:
            return ""
        p = ent["perm"]
        return perm_brief(p["title"], p["body"] or p["summary"] or p["question"])

    def perm_view(self, sid, state):
        """The current permission request as the mini-app object, or None."""
        if is_sdk(sid):
            pend = self.perm_pending(sid)
            return sdk_perm_view(pend[0]) if pend else None
        if is_herdr(sid) or state != "blocked" or not self.is_live(sid):
            return None
        ent = self._perm_cache.get(sid)
        if ent and self.clock() - ent["ts"] <= PERM_FRESH:
            return tmux_perm_view(ent["perm"])
        p = parse_perm_dialog(self.tmux.capture(sid))  # a plain capture: a missing window is not reopened
        return tmux_perm_view(p) if p else None

    def perm_mark_answered(self, sid, perm):
        """The dialog we have just answered may still be drawn for a moment: scan must not cache/push it again."""
        self._perm_cache.pop(sid, None)
        self._perm_answered[sid] = (perm_sig(perm), self.clock() + PERM_ANSWERED_TTL)
        self.perm_msgs_close(sid, _t("✔ Decided"))

    def _drop_pending(self, sid, req):
        k = self.known.get(sid)
        if k is not None:
            k["pending"] = [r for r in (k.get("pending") or []) if not (isinstance(r, dict) and str(r.get("req")) == str(req))]

    def perm_scan(self, sessions, prime=False):
        """Background: refreshes the cache of tmux dialogs for blocked live sessions. A new dialog is not pushed
        when prime is set (first tick after a start)."""
        seen = set()
        for s in sessions:
            sid = s["id"]
            if (s.get("state") != "blocked" or is_sdk(sid) or is_herdr(sid) or self.is_old(sid)
                    or self.is_archived(sid) or not self.is_live(sid)):
                continue
            pane = self.tmux.capture(sid)
            if not pane:  # a background session nobody attached to: its dialog is only on a screen
                if (s.get("needs") or "").startswith("approve "):  # blocked alone also means a finished turn
                    self.perm_open(sid, prime)
                if sid in self._perm_cache:
                    seen.add(sid)  # the window is gone (idle cleanup), not the dialog: keep its notice
                continue
            perm = parse_perm_dialog(pane)
            if perm is None:
                continue
            sig = perm_sig(perm)
            ans = self._perm_answered.get(sid)
            if ans and ans[0] == sig and self.clock() < ans[1]:
                continue  # the dialog we have just answered, still on the screen
            seen.add(sid)
            self.tmux.last_used[self.tmux.name(sid)] = self.tmux.clock()  # an open dialog keeps its window
            ent = self._perm_cache.get(sid)
            if ent and ent["sig"] == sig:
                ent["perm"], ent["ts"] = perm, self.clock()
            else:
                # opened for a dialog that was there at start, or already notified before a restart: no repeat
                old = (bool(prime) or sid in self._perm_prime
                       or any(k[0] == sid and r["sig"] == sig for k, r in self._perm_msgs.items()))
                self._perm_cache[sid] = {"perm": perm, "sig": sig, "pushed": old, "ts": self.clock()}
            self._perm_prime.discard(sid)
        for sid in list(self._perm_cache):
            if sid not in seen:
                self._perm_cache.pop(sid, None)
        for (sid, _uid), rec in list(self._perm_msgs.items()):
            ent = self._perm_cache.get(sid)
            if not (ent and ent["sig"] == rec["sig"]):  # answered elsewhere or gone: no live buttons on a closed dialog
                self.perm_msgs_close(sid, _t("closed"), only_sig=rec["sig"])

    def perm_msgs_load(self):
        """Notices survive a restart, so the tick still collapses those whose dialog closed while the bot was away."""
        return {(r["sid"], r["uid"]): {k: r[k] for k in ("mid", "sig", "head")}
                for r in self.store.data.get("perm_msgs", []) if isinstance(r, dict) and {"sid", "uid", "mid"} <= r.keys()}

    def perm_msgs_save(self):
        self.store.data["perm_msgs"] = [dict(rec, sid=k[0], uid=k[1]) for k, rec in self._perm_msgs.items()]
        self.store.save()

    def perm_open(self, sid, prime=False):
        """Opens a tmux window for a blocked session (in the background, at most once a minute), so the next
        tick sees the dialog."""
        if self.clock() - self._perm_opened.get(sid, -PERM_OPEN_RETRY) < PERM_OPEN_RETRY:
            return
        self._perm_opened[sid] = self.clock()
        if prime:
            self._perm_prime.add(sid)

        def open_():
            with self.tmux.lock(sid):
                if self.tmux.run(["tmux", "has-session", "-t", "=" + self.tmux.name(sid)])[0] != 0:
                    self.tmux._open_window(sid)
        self.spawn(open_)

    def perm_msg_track(self, uid, sid, ent, r):
        mid = r.get("message_id") if isinstance(r, dict) else None
        if mid:
            p = ent["perm"]
            self._perm_msgs[(sid, uid)] = {"mid": mid, "sig": ent["sig"],
                                           "head": " · ".join(x for x in ("🔐 " + p["title"], short(p["body"] or p["summary"], 80)) if x)}
            self.perm_msgs_save()

    def perm_msgs_close(self, sid, note, only_sig=None):
        """A permission notice of a tmux dialog that is no longer open becomes one line without buttons."""
        for key, rec in list(self._perm_msgs.items()):
            if key[0] != sid or (only_sig is not None and rec["sig"] != only_sig):
                continue
            self._perm_msgs.pop(key, None)
            self.perm_msgs_save()
            try:
                self.tg.edit(key[1], rec["mid"], rec["head"] + " · " + note)
            except TGError as e:
                log("perm close failed: %s" % e)

    def perm_push(self, sid, title, detail):
        """A push for a permission request: it blocks the work, so it goes to the owner whether or not the session
        is subscribed to. -> set of chats it reached."""
        body = perm_push_text(title, detail)
        head = _t("%s: needs permission") % short(self.session_name(sid), 50)
        return {uid for uid in sorted(self.cfg.allowed) if self.push_notify(uid, sid, body, "high", title=head)}

    def perm_notice(self, sid, name, p):
        """(text, pre, markup) of the Telegram notification for a tmux permission dialog."""
        text = _t("🔐 %s: needs your permission — %s") % (name, p["title"]) + "".join(
            "\n" + x for x in (p["summary"], p["note"]) if x)
        return text, trim_screen(p["body"], 25, 1500) or None, perm_markup(sid, p)

    def notice_send(self, uid, sid, text, pre, markup, final=False, perm_only=False):
        """One notification into a chat/topic (a screen excerpt goes into <pre>); it replaces the old one of the session.
        -> the sent message (perm_only: the topic made for it does not subscribe to the session)."""
        if self.topics_enabled:
            self.ensure_topic(uid, sid, perm_only=perm_only)
            self.touch_topic(uid, sid)
        if pre:
            html = esc(text) + "\n\n<pre>%s</pre>" % esc(pre)
            r = self._threaded(uid, sid, lambda th: self.tg.send_rich(uid, html, text + "\n\n" + pre, markup, thread=th))
            self.remember(uid, [r], sid)
        else:
            r = self.say(uid, text, markup, sid=sid)
        self.swap_note(uid, sid, r, final)
        return r

    def perm_push_pending(self):
        """A tmux dialog that appeared without a state change: one push per dialog; without a delivered push it goes
        to Telegram with its buttons (and replaces the old «waiting» notification)."""
        for sid, ent in list(self._perm_cache.items()):
            if ent["pushed"] or self.webapp_active(sid):
                continue  # the owner is in the mini-app right now: decided on a later tick
            ent["pushed"] = True
            p = ent["perm"]
            got = self.perm_push(sid, p["title"], p["body"] or p["summary"] or p["question"])
            text, pre, markup = self.perm_notice(sid, self.session_name(sid), p)
            for uid in sorted(self.cfg.allowed):
                if uid in got or self.question_shown(uid, sid):
                    continue
                try:
                    r = self.notice_send(uid, sid, text, pre, markup,
                                         perm_only=not self.tracked_c(self.store.chat(uid), sid))
                    self.perm_msg_track(uid, sid, ent, r)
                except TGError as e:
                    log("perm notice failed: %s" % e)

    def sdk_perm_push(self, sid, req):
        """-> set of chats the push reached (empty: nothing sent)."""
        if (not isinstance(req, dict) or req.get("kind") != "perm" or not req.get("req")
                or self.is_old(sid) or self.is_archived(sid)):
            return set()
        key = (sid, str(req["req"]))
        if key in self._perm_pushed or self.webapp_active(sid):
            return set()
        self._perm_pushed.add(key)
        if len(self._perm_pushed) > 500:
            self._perm_pushed = {key}
        head, body = sdk_input_parts(req.get("tool"), req.get("input"))
        return self.perm_push(sid, str(req.get("tool") or "?"), body or head)

    def perm_press(self, chat, mid, sid, n, h):
        """A Telegram button of a tmux permission dialog: the same label check as in the mini-app."""
        if sid not in self.known or is_sdk(sid) or is_herdr(sid):
            self.popup(chat, _t("This button is outdated."))
            return
        with self.tmux.lock(sid):
            perm = parse_perm_dialog(self.tmux.capture(sid))
            opt = next((o for o in perm["options"] if o["n"] == n), None) if perm else None
            if perm is None:
                err = _t("The permission request is already closed.")
            elif opt is None or perm_label_hash(opt["label"]) != h:
                err = _t("This button is outdated.")
            else:
                err = None if self.tmux.keys(sid, str(n)) else "⚠️ " + _t("The key was not pressed.")
        if err:
            self.popup(chat, err, sid=sid)
            return
        self.perm_mark_answered(sid, perm)
        if mid:
            self.tg.edit_markup(chat, mid)
        self.popup(chat, _t("Sent: %s") % short(opt["label"], 60), sid=sid)

    def api_perm(self, uid, sid, body):
        """POST /api/session/<id>/perm -> (HTTP code, object). See the contract in the docs of the mini-app."""
        self.webapp_touch[sid] = self.clock()
        opt = body.get("option")
        if isinstance(opt, bool) or not isinstance(opt, int) or not 1 <= opt <= 9:
            return 400, {"ok": False, "code": "bad", "error": _t("No such option")}
        if is_sdk(sid):
            return self.api_perm_sdk(sid, opt, body.get("req"))
        if is_herdr(sid):
            return 200, {"ok": False, "code": "error", "error": _t("Not available for herdr agents")}
        label = body.get("label")
        if not isinstance(label, str):
            return 400, {"ok": False, "code": "bad", "error": _t("No such option")}
        if not self.is_live(sid):
            return 200, {"ok": False, "code": "stopped", "error": _t("The session is stopped — command not sent")}
        with self.tmux.lock(sid):
            perm = parse_perm_dialog(self.tmux.capture(sid))
            if perm is None:
                self._perm_cache.pop(sid, None)
                return 200, {"ok": False, "code": "closed"}
            cur = next((o for o in perm["options"] if o["n"] == opt), None)
            if cur is None or cur["label"] != label:
                return 200, {"ok": False, "code": "stale", "perm": tmux_perm_view(perm)}
            if not self.tmux.keys(sid, str(opt)):
                return 200, {"ok": False, "code": "error", "error": _t("The key was not pressed.")}
            self.perm_mark_answered(sid, perm)
            self.sleep(1.0)
            new = parse_perm_dialog(self.tmux.capture(sid))
        return 200, {"ok": True, "perm": tmux_perm_view(new) if new else None}

    def api_perm_sdk(self, sid, opt, req):
        """Not under api_lock: the runner call may take a while."""
        if opt not in (1, 2, 3):
            return 400, {"ok": False, "code": "bad", "error": _t("No such option")}
        pend = self.perm_pending(sid)
        req = str(req) if req else (str(pend[0]["req"]) if pend else None)
        cur = next((r for r in pend if str(r["req"]) == req), None) if req else None
        if cur is None:  # the cache may be behind: ask the runner
            try:
                x = self.sdk.get(sid)
            except SdkError as e:
                return 200, {"ok": False, "code": "error", "error": sdk_err_text(e)}
            pend = [r for r in ((x or {}).get("pending") or []) if isinstance(r, dict) and r.get("kind") == "perm"
                    and r.get("req")]
            req = req or (str(pend[0]["req"]) if pend else None)
            cur = next((r for r in pend if str(r["req"]) == req), None) if req else None
            if cur is None:
                return 200, {"ok": False, "code": "closed"}
        if opt == 2 and not cur.get("can_always"):
            return 400, {"ok": False, "code": "bad", "error": _t("No such option")}
        decision = {1: "allow", 2: "always", 3: "deny"}[opt]
        try:
            res = self.sdk.call("session.answer", {"id": sid, "req": req, "decision": decision}, timeout=8.0)
        except SdkError as e:
            return 200, {"ok": False, "code": "error", "error": sdk_err_text(e)}
        self._drop_pending(sid, req)
        if res.get("already"):
            return 200, {"ok": True, "already": True}
        rest = [r for r in pend if str(r["req"]) != req]
        return 200, {"ok": True, "perm": sdk_perm_view(rest[0]) if rest else None}

    def api_command(self, uid, sid, text):
        """A slash command into a live session (same path as /rename). -> (HTTP code, object)."""
        self.webapp_touch[sid] = self.clock()
        if is_sdk(sid):
            return 200, {"ok": False, "code": "error", "error": _t("Not available for SDK sessions")}
        if is_herdr(sid):
            return 200, {"ok": False, "code": "error", "error": _t("Not available for herdr agents")}
        text = text.strip() if isinstance(text, str) else ""
        if (not text.startswith("/") or "\n" in text or "\r" in text or len(text) > 200
                or text.split()[0] not in command_names()):
            return 400, {"ok": False, "code": "bad",
                         "error": _t("A command from the list, on one line, up to 200 characters")}
        if not self.is_live(sid):
            return 200, {"ok": False, "code": "stopped", "error": _t("The session is stopped — command not sent")}
        ok, err = self.injector(sid, text)
        if not ok:
            if err == ERR_MENU:
                scr = self.api_screen(sid)
                return 200, {"ok": False, "code": "menu", "menu": True, "screen": scr.get("screen"),
                             "error": _t("The session is waiting for an answer to a question")}
            return 200, {"ok": False, "code": "error", "error": err}
        for _ in range(8):  # up to 2 s: a command may open a menu (/model, /usage…)
            self.sleep(0.25)
            pane = self.tmux.capture(sid)
            if dialog_active(pane):
                return 200, {"ok": True, "menu": True, "screen": trim_screen(pane, 30, 6000)}
        return 200, {"ok": True, "menu": False}

    def api_key(self, sid, key):
        self.webapp_touch[sid] = self.clock()
        mapped = KEY_MAP.get(key) if isinstance(key, str) else None
        if mapped is None:
            return 400, {"ok": False, "error": _t("Key not allowed")}
        if is_sdk(sid):
            return 200, {"ok": False, "menu": False, "screen": None, "error": _t("Not available for SDK sessions")}
        if not self.is_live(sid):
            return 200, {"ok": False, "menu": False, "screen": None, "stopped": True}
        ok, err = self.tmux.send_key(sid, mapped)
        if not ok:
            return 200, {"ok": False, "error": err, "menu": False, "screen": None}
        self.sleep(0.6)
        res = self.api_screen(sid)
        res["ok"] = True
        return 200, res

    def model_keys_path(self):
        return os.path.join(os.path.dirname(self.cfg.sdk_env), "model-keys.env")

    def model_keys(self):
        """Stored keys: the one setup.sh pointed the runner at, then the sheet's own file."""
        return {**env_read(os.path.expanduser(env_read(self.cfg.sdk_env).get("CC_SDK_KEY_FILE", ""))), **env_read(self.model_keys_path())}

    def api_model_get(self):
        """The current model: what the sheet shows. A key is never returned, only whether it is stored."""
        env = env_read(self.cfg.sdk_env)
        keys = self.model_keys()
        base, model = env.get("CC_SDK_BASE_URL", ""), env.get("CC_SDK_MODEL", "")
        pid = "claude_login" if env.get("CC_SDK_AUTH") == "login" else next(
            (k for k, p in MODEL_PRESETS.items() if p["base"] == base and p["base"] is not None and k != "custom"), "custom")
        try:
            busy = sum(x.get("state") == "working" for x in (self.sdk.sessions() or [])) if self.sdk else 0
        except (HerdrError, AttributeError):
            busy = 0
        presets = [{"id": k, "title": p["title"], "model": p["model"], "stored": bool(p["var"] and keys.get(p["var"]))}
                   for k, p in MODEL_PRESETS.items() if p["base"] is not None]  # Codex answers in the group only
        presets.insert(0, dict(CLAUDE_LOGIN, stored=False, login=True))
        for p in presets:
            p["models"] = MODEL_CHOICES.get(p["id"], [])
        group = {"on": bool(self.cfg.responder_token and self.cfg.responder_chat is not None),
                 "engine": self.cfg.responder_engine, "codex_model": self.cfg.codex_model}
        return 200, {"provider": pid, "model": model, "base": base, "busy": busy, "presets": presets, "group": group}

    def api_model_save(self, body):
        """Saved only after the provider has accepted the key; then the runner restarts on it."""
        if body.get("target") == "group" and body.get("engine") == "sdk":  # the group answers on the sessions' model
            self.cfg.responder_engine = "sdk"
            env_update(self.cfg.env_path, {"CC_RESPONDER_ENGINE": None})
            return 200, {"ok": True, "restarted": True}
        if body.get("provider") == "claude_login":  # the CLI's own sign-in: nothing to store but the model
            model = str(body.get("model") or CLAUDE_LOGIN["model"]).strip()
            if not _MODEL_NAME.fullmatch(model):
                return 200, {"ok": False, "error": "model"}
            rc, out = self.run([self.cfg.claude_bin, "auth", "status"], timeout=20)
            try:
                signed = rc == 0 and json.loads(out).get("loggedIn") is True
            except (ValueError, AttributeError):
                signed = False
            if not signed:
                return 200, {"ok": False, "error": "claude_login"}
            env_update(self.cfg.sdk_env, {"CC_SDK_AUTH": "login", "CC_SDK_MODEL": model, "CC_SDK_BASE_URL": None})
            rc, _ = self.run(["systemctl", "--user", "restart", "zidos-pager-sdk"], timeout=30)
            return 200, {"ok": True, "restarted": rc == 0}
        preset = MODEL_PRESETS.get("codex" if body.get("target") == "group" else body.get("provider"))
        if preset is None:
            return 400, {"ok": False, "error": "provider"}
        if preset["base"] is None:  # Codex: its own login on this machine, group answers only
            rc, _ = self.run(["codex", "login", "status"], timeout=20)
            if rc != 0:
                return 200, {"ok": False, "error": "codex"}
            model = str(body.get("model") or "").strip()  # empty: the default from ~/.codex/config.toml
            if model and not _MODEL_NAME.fullmatch(model):
                return 200, {"ok": False, "error": "model"}
            self.cfg.responder_engine, self.cfg.codex_model = "codex", model
            env_update(self.cfg.env_path, {"CC_RESPONDER_ENGINE": "codex", "CC_CODEX_MODEL": model or None})
            return 200, {"ok": True, "restarted": True}
        model = str(body.get("model") or preset["model"]).strip()
        base = str(body.get("base") or "").strip() if body.get("provider") == "custom" else preset["base"]
        key = str(body.get("key") or "").strip()
        if not _MODEL_NAME.fullmatch(model):
            return 200, {"ok": False, "error": "model"}
        if body.get("provider") == "custom" and not re.fullmatch(r"https://[^\s\"'\\]+", base):  # owner-only, still no plain http for a key
            return 200, {"ok": False, "error": "base"}
        if key and not _MODEL_KEY.fullmatch(key):
            return 200, {"ok": False, "error": "key"}
        key = key or self.model_keys().get(preset["var"], "")
        if not key:
            return 200, {"ok": False, "error": "key"}
        verdict = model_check(base, key, model)
        if verdict != "ok":
            return 200, {"ok": False, "error": verdict}
        env_update(self.model_keys_path(), {preset["var"]: key})
        env_update(self.cfg.sdk_env, {"CC_SDK_KEY_FILE": self.model_keys_path(), "CC_SDK_KEY_VAR": preset["var"],
                                      "CC_SDK_MODEL": model, "CC_SDK_BASE_URL": base or None, "CC_SDK_AUTH": None})
        rc, _ = self.run(["systemctl", "--user", "restart", "zidos-pager-sdk"], timeout=30)
        return 200, {"ok": True, "restarted": rc == 0}

    def api_bots(self, method, action, body):
        """GET /api/bots, POST /api/bots/add|save|persona|remove: the same functions as the `bots` command (slow: no api_lock)."""
        if method == "GET" and not action:
            return 200, {"bots": bots_list(self.cfg)["bots"],
                         "presets": [{"id": k, "title": p["title"], "model": p["model"]} for k, p in MODEL_PRESETS.items()]}
        if method != "POST" or action not in BOTS_ACTIONS:
            return 405, {"error": "method"}
        return 200, BOTS_ACTIONS[action](self.cfg, body)

    def api_dirs(self):
        return {"dirs": [tilde(d) for d in self.recent_dirs(cached=True)]}

    def api_new(self, uid, body):
        text = str(body.get("text") or "").strip()
        if not text:
            return 400, {"ok": False, "error": _t("Describe the task")}
        cwd = os.path.expanduser(str(body.get("cwd") or self.cfg.default_cwd))
        if not os.path.isdir(cwd):
            return 400, {"ok": False, "error": _t("Folder not found")}
        sdk = body.get("sdk") is True
        if sdk and not self.sdk_ready(True):
            return 400, {"ok": False, "error": _t("The SDK runner is not available")}
        sid = self.do_new(uid, cwd, text, *((True,) if sdk else ()))
        if not sid:
            return 502, {"ok": False, "error": _t("Could not create the session")}
        return 200, {"ok": True, "id": sid}

    def api_dispatch(self, uid, method, path, body, device=None):
        """-> (HTTP code, JSON object)."""
        parts = [x for x in path.split("/") if x]
        if parts[:1] != ["api"]:
            return 404, {"error": "not found"}
        if parts == ["api", "sessions"]:
            return (200, self.api_sessions(uid)) if method == "GET" else (405, {"error": "method"})
        if parts == ["api", "commands"]:
            return (200, {"commands": slash_commands()}) if method == "GET" else (405, {"error": "method"})
        if parts == ["api", "model"]:
            return self.api_model_get() if method == "GET" else self.api_model_save(body)
        if parts[:2] == ["api", "bots"] and len(parts) <= 3:
            return self.api_bots(method, parts[2] if len(parts) == 3 else "", body)
        if parts == ["api", "archive_done"]:
            if method != "POST":
                return 405, {"error": "method"}
            with self.api_lock:
                n = self.archive_done()
            return 200, {"ok": True, "count": n}
        if parts == ["api", "push", "key"]:
            return self.api_push_key() if method == "GET" else (405, {"error": "method"})
        if parts == ["api", "push", "subscribe"]:
            return self.api_push_subscribe(uid, device, body) if method == "POST" else (405, {"error": "method"})
        if parts == ["api", "push", "unsubscribe"]:
            return self.api_push_unsubscribe(uid, body) if method == "POST" else (405, {"error": "method"})
        if parts == ["api", "voice"]:  # recognition is slow: no api_lock (like /perm)
            if method == "GET":
                return 200, {"voice": self.stt_ready()}
            return self.api_voice(uid, body) if method == "POST" else (405, {"error": "method"})
        if parts == ["api", "dirs"]:
            return (200, self.api_dirs()) if method == "GET" else (405, {"error": "method"})
        if parts == ["api", "new"]:
            if method != "POST":
                return 405, {"error": "method"}
            with self.api_lock:
                return self.api_new(uid, body)
        if len(parts) in (3, 4, 5) and parts[1] == "session":
            sid = parts[2]
            if sid not in self.known:
                return 404, {"error": "no such session"}
            if len(parts) == 3:
                return (200, self.api_session(uid, sid)) if method == "GET" else (405, {"error": "method"})
            if parts[3] == "subagents" and len(parts) == 4:  # read-only
                return (200, self.api_subagents(sid)) if method == "GET" else (405, {"error": "method"})
            if parts[3] == "subagent" and len(parts) == 5:
                return self.api_subagent(sid, parts[4]) if method == "GET" else (405, {"error": "method"})
            if len(parts) == 5:
                return 404, {"error": "not found"}
            action = parts[3]
            if action == "screen":
                if method != "GET":
                    return 405, {"error": "method"}
                with self.api_lock:
                    return 200, self.api_screen(sid)
            if method != "POST":
                return 405, {"error": "method"}
            if action == "perm":  # waits for the screen / the runner: only tmux.lock(sid), not the global lock
                return self.api_perm(uid, sid, body)
            if action == "send":  # the paste waits for the screen (1–2 s): only tmux.lock(sid), like /perm
                return 200, self.api_send(uid, sid, body.get("text"))
            with self.api_lock:
                if action == "upload":
                    return self.api_upload(uid, sid, body)
                if action == "sendfile":
                    return self.api_sendfile(uid, sid, body.get("path"))
                if action == "command":
                    return self.api_command(uid, sid, body.get("text"))
                if action == "key":
                    return self.api_key(sid, body.get("key"))
                if action == "answer":
                    return 200, self.api_answer(uid, sid, body)
                if action in ("watch", "rename"):
                    self.webapp_touch[sid] = self.clock()
                if action == "watch":
                    self.set_watch(uid, sid, bool(body.get("on")))
                    return 200, {"ok": True, "watched": self.is_watched(sid)}
                if action == "rename":
                    status = self.apply_name(sid, str(body.get("name") or ""), manual=True)
                    return 200, {"ok": status != "empty", "status": status,
                                 "error": _t("Empty name") if status == "empty" else None}
                if action == "stop":
                    self.webapp_touch[sid] = self.clock()
                    ok = self.stop_session(sid)
                    return 200, {"ok": ok, "stopped": ok,
                                 "error": None if ok else _t("claude stop failed")}
                if action == "archive":
                    self.webapp_touch[sid] = self.clock()
                    ok, stopped, err = self.archive_session(sid, stop=body.get("stop", True) is not False)
                    return 200, {"ok": ok, "stopped": stopped, "error": err}
                if action == "unarchive":
                    self.webapp_touch[sid] = self.clock()
                    self.unarchive_session(sid)
                    return 200, {"ok": True}
        return 404, {"error": "not found"}

    # ---- background tasks
    def forward_tick(self):
        now = self.clock()
        for uid in self.cfg.allowed:
            c = self.store.chat(uid)
            for sid in list(self.store.data.get("watched", [])) + [k for k, t in c["topics"].items()
                                                                  if not t.get("perm_only")]:
                if sid not in c["fw"]:
                    self.init_fw(c, sid)
        for chat_s, c in list(self.store.data["chats"].items()):
            for sid, fw in list(c.get("fw", {}).items()):
                if (fw.get("until") and fw["until"] < now and sid != c.get("session")
                        and not self.is_watched(sid) and sid not in c.get("topics", {})):
                    c["fw"].pop(sid, None)
                    continue
                self.forward_one(int(chat_s), sid, fw)

    def forward_one(self, chat, sid, fw):
        path = self.transcript_path(sid)
        if not path:
            return
        if fw.get("offset") is None:
            try:
                fw["offset"] = os.path.getsize(path)
            except OSError:
                return
            self.store.save()
            return
        if fw.pop("scan_ask", False) and self.tracked_c(self.store.chat(chat), sid):
            ev = unanswered_ask(path)
            if ev:
                try:
                    self.on_ask(chat, sid, ev)
                except TGError as e:
                    log("backlog question failed: %s" % e)
        events, new_off = read_new_events(path, fw["offset"])
        info = self.session_info(sid)
        seen = fw.setdefault("seen", [])
        interested = self.tracked_c(self.store.chat(chat), sid)
        header = "%s %s" % ("❓" if events and self.is_asking(sid, info) else EMOJI.get(info["state"], "🔵"),
                            info["name"])
        # an intermediate text (the model is still working) is silent; a finished/waiting one rings.
        # A question with options rings with its own message, so the text before it stays quiet.
        asks = interested and any(e["kind"] == "ask" for e in events)
        quiet = info["state"] == "working" or bool(asks)
        for ev in events:
            kind = ev["kind"]
            try:
                if kind == "text":
                    uid = ev["uuid"]
                    if uid and uid in seen:
                        continue
                    if uid:
                        seen.append(uid)
                    if not fw.get("turn"):
                        continue
                    self.say_md(chat, ev["text"], header=header, sid=sid, silent=quiet,
                                reply_to=self.reply_to.pop((chat, sid), None),
                                doc_name=re.sub(r"\W+", "_", info["name"])[:40] + ".md")
                    self.last_forward[sid] = self.clock()
                    self.last_forward_text[sid] = ev["text"]
                    self.turn_forwarded.add(sid)
                    self.touch_topic(chat, sid)
                elif kind == "ask":
                    if interested:
                        self.on_ask(chat, sid, ev)
                elif kind == "answer":
                    self.on_answer(ev)
                elif kind == "turn_end":
                    fw["turn"] = False
            except TGError as e:
                log("forward failed: %s" % e)
        del seen[:-SEEN_LIMIT]
        fw["offset"] = new_off
        if fw.get("turn"):
            if info["state"] == "working":
                fw["turn_w"] = True
            elif not events and (fw.get("turn_w") or self.clock() - fw.get("turn_started", 0) > 30):
                fw["turn"] = False
        self.store.save()

    def status_tick(self):
        now = self.clock()
        for key, st in list(self.status.items()):
            chat = key[0]
            info = self.session_info(st["sid"])
            if info["state"] == "working":
                st["seen_working"] = True
                text = _t("🔵 %s is working: %s") % (info["name"], short(info["detail"], 200) or "…")
                try:
                    if not st["mid"]:
                        r = self.say(chat, text, sid=st["sid"], silent=True)
                        st["mid"] = r.get("message_id") if isinstance(r, dict) else None
                        st["text"], st["edited"] = text, now
                    elif text != st["text"] and now - st["edited"] >= 5:
                        self.tg.edit(chat, st["mid"], text)
                        st["text"], st["edited"] = text, now
                    if now - st["typed"] >= 5:
                        self.tg.typing(chat, self._th(chat, st["sid"]))
                        st["typed"] = now
                except TGError as e:
                    log("status failed: %s" % e)
            elif st["seen_working"] or now - st["since"] > 15:
                if st["mid"]:
                    self.tg.delete(chat, st["mid"])
                self.status.pop(key, None)

    def notify_tick(self):
        with self._notify_lock:  # the poll loop and the herdr event thread share one state diff: one notification
            self._notify_tick()

    def _notify_tick(self):
        sessions = self.list_sessions(show_all=True)
        if sessions is None:
            return
        before = self.store.data["states"]
        trans, merged = find_transitions(before, sessions)
        self.store.data["states"] = merged
        self.store.save()
        self.perm_scan(sessions, prime=not self.primed)
        if not self.primed:
            self.primed = True
            return
        for s in trans:
            if not self.is_watched(s["id"]) or self.is_old(s["id"]) or self.is_archived(s["id"]):
                continue
            sid = s["id"]
            if is_herdr(sid) and sid not in before:
                continue  # a herdr pane seen for the first time is not a transition
            if s.get("source") == "sdk" and s["state"] == "blocked" and s.get("pending"):
                continue  # a request is waiting: it was shown with its own buttons
            forwarded = sid in self.turn_forwarded  # the forwarder has already shown this turn's replies
            self.turn_forwarded.discard(sid)
            if self.webapp_recent(sid):
                continue  # the owner has just acted in the mini-app — transitions are not duplicated
            if forwarded and s["state"] == "done":
                continue
            path = self.transcript_path(sid)
            last = (tail_texts(path, 1) or [(None, None)])[-1][1] if path else None
            text = notification_text(s, last=last)
            pre = None
            if is_herdr(sid):  # the question text is not in the status: show the last screen lines
                ok, scr = self.herdr_screen(sid)
                pre = trim_screen(scr, 15, 1500) if ok else None
            if forwarded and self.last_forward_text.get(sid) == text:
                continue
            if is_sdk(sid):  # the cost of the turn lives here, not in a message of its own
                cost = sdk_cost_line(self.known.get(sid) or s)
                if cost:
                    text += "\n\n" + cost
            markup = notify_markup(s, self.topics_enabled)
            push_body = {"blocked": _t("is waiting for you: %s") % (humanize_detail(s["detail"]) or _t("needs an answer")),
                         "done": _t("done"),
                         "failed": _t("error: %s") % (humanize_detail(s["detail"]) or _t("see details"))}.get(
                             s["state"], s["state"])
            ent = self._perm_cache.get(sid) if s["state"] == "blocked" else None
            if ent:  # a tmux permission dialog: show it with its own buttons instead of «Yes / No»
                p = ent["perm"]
                text, pre, markup = self.perm_notice(sid, s["name"], p)
                push_body = perm_push_text(p["title"], p["body"] or p["summary"] or p["question"])
                push_title = _t("%s: needs permission") % short(s["name"], 50)
            else:
                push_title = None
            for uid in sorted(self.cfg.allowed):
                if s["state"] == "blocked" and self.question_shown(uid, sid):
                    continue  # the open question is already in the chat with its own buttons
                if self.push_notify(uid, sid, push_body, "normal" if s["state"] == "done" else "high", title=push_title):
                    continue  # PWA instead of Telegram: delivered — not duplicated in Telegram
                try:
                    r = self.notice_send(uid, sid, text, pre, markup, s["state"] in ("done", "failed")
                                         and bool(s.get("result") or last or s.get("detail")))
                    if ent:
                        self.perm_msg_track(uid, sid, ent, r)
                except TGError as e:
                    log("notify failed: %s" % e)
            if ent:
                ent["pushed"] = True  # the notification above carried the dialog
        self.perm_push_pending()

    def swap_note(self, uid, sid, r, final=False):
        """One live notification per session in the chat: the new one rings the phone, the old service one goes.
        A notification that carries a result or an error text (final) is never deleted."""
        mid = r.get("message_id") if isinstance(r, dict) else None
        if not mid:
            return
        notes = self.store.chat(uid)["notes"]
        prev = notes.get(sid)
        prev = prev if isinstance(prev, dict) else ({"mid": prev} if prev else None)
        put_capped(notes, sid, {"mid": mid, "final": bool(final)})
        self.store.save()
        if prev and prev["mid"] != mid and not prev.get("final"):
            self.tg.delete(uid, prev["mid"])

    # ---- StopFailure hook queue: one JSON line per session stopped by a rate limit / billing error
    def stopfail_text(self, rec):
        sid = str(rec.get("session_id") or "")
        name = self.display_name(sid) if sid in self.known else sid[:8]
        err = str(rec.get("error") or "")
        reason = {"rate_limit": _t("rate limit (rate_limit)"),
                  "billing_error": _t("billing (billing_error)")}.get(err, err or "?")
        lines = [_t("⛔ Session «%s» stopped: %s") % (name or _t("session"), reason)]
        detail = short(str(rec.get("details") or rec.get("message") or ""), 300)
        if detail:
            lines.append(detail)
        t = rec.get("time")
        try:
            ts = float(t) if isinstance(t, (int, float)) else _to_epoch(t)
            if ts > 0:
                lines.append(time.strftime("%d.%m %H:%M", time.localtime(ts)))
        except (ValueError, TypeError, OverflowError, OSError):
            if t:
                lines.append(short(str(t), 40))
        return "\n".join(lines)

    def stopfail_tick(self):
        path = self.cfg.stopfail_queue
        st = self.store.data.setdefault("stopfail", {})
        try:
            with open(path, "rb") as f:
                fs = os.fstat(f.fileno())
                ident = [fs.st_ino, fs.st_dev]
                off = st.get("off")
                if off is None or st.get("id") is None:
                    st.update({"off": fs.st_size, "id": ident})  # first run: the old queue is not replayed
                    self.store.save()
                    return
                if st.get("id") != ident or fs.st_size < off:
                    off = 0  # rotated or truncated: read the new file from the start
                f.seek(off)
                chunk = f.read(1 << 20)
        except FileNotFoundError:
            if st.get("id") is None:
                st.update({"off": 0, "id": "missing"})  # the file that appears later is all new: read it from the start
                self.store.save()
            return
        except OSError:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            if st.get("id") != ident or st.get("off") != off:
                st.update({"off": off, "id": ident})
                self.store.save()
            return  # no complete line yet: wait for the next tick
        recs = []
        for raw in chunk[:end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
                if not isinstance(rec, dict):
                    raise ValueError("not an object")
                recs.append(rec)
            except ValueError:
                log("stopfail: skipped a broken line")
        st.update({"off": off + end + 1, "id": ident})
        now = self.clock()
        last = {k: t for k, t in (st.get("last") or {}).items() if now - t < STOPFAIL_REPEAT}
        news = []
        for rec in recs:
            key = "%s %s" % (rec.get("session_id"), rec.get("error"))
            if key not in last:
                last[key] = now
                news.append(rec)
        st["last"] = last
        self.store.save()
        for rec in news:
            sid = str(rec.get("session_id") or "")
            text = self.stopfail_text(rec)
            for uid in sorted(self.cfg.allowed):
                try:
                    self.say(uid, text, sid=sid if sid in self.known else None)
                except TGError as e:
                    log("stopfail notify failed: %s" % e)

    # ---- herdr event stream: wakes the notifier at once (the poll loop stays as the safety net)
    def herdr_on_event(self, ev):
        """-> True when the event is an agent going from working to a state that waits for the owner."""
        name = str(ev.get("event") or "").replace(".", "_")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        pane = str(data.get("pane_id") or "")
        if name != "pane_agent_status_changed" or not pane:
            return False
        new = data.get("agent_status")
        old = self._hd_status.get(pane)
        self._hd_status[pane] = new
        if old == "working" and new in ("blocked", "idle", "done"):
            self.notify_tick()
            return True
        return False

    def herdr_watch_once(self):
        """One subscription session; returns when the set of panes changed (the caller resubscribes at once)."""
        h = self.herdr
        panes = sorted(str(p["pane_id"]) for p in h.panes())
        for a in h.agents():  # statuses before the first event, so working -> blocked is seen as a transition
            self._hd_status.setdefault(str(a["pane_id"]), a.get("agent_status"))
        subs = [{"type": "pane.created"}, {"type": "pane.agent_detected"}, {"type": "pane.closed"}]
        subs += [{"type": "pane.agent_status_changed", "pane_id": p} for p in panes]
        for ev in h.subscribe(subs, self.stop_event):
            name = str(ev.get("event") or "").replace(".", "_")
            if name in ("pane_created", "pane_agent_detected", "pane_closed"):
                return
            self.herdr_on_event(ev)

    def herdr_watch(self):
        while not self.stop_event.is_set():
            wait = 5
            if self.herdr is not None and self.herdr.enabled():
                try:
                    self.herdr_watch_once()
                    wait = 0.5  # new pane: resubscribe quickly
                except HerdrError as e:
                    self.herdr_quiet(e)
                except Exception as e:
                    log("herdr watch error: %s" % type(e).__name__)
            self.stop_event.wait(wait)

    def _loop(self, fn, period, name):
        while not self.stop_event.is_set():
            try:
                fn()
            except Exception as e:
                log("%s error: %s" % (name, type(e).__name__))
            self.stop_event.wait(period)

    def poll_once(self):
        offset = self.store.data.get("offset", 0)
        updates = self.tg.get_updates(offset) or []
        for u in updates:
            try:
                self.handle_update(u)
            except TGError as e:
                log("handler tg error: %s" % e)
            except Exception as e:
                log("handler error: %s" % type(e).__name__)
            self.store.data["offset"] = u["update_id"] + 1
            self.store.save()
        return len(updates)

    def start_responder(self):
        """The responder role: getUpdates, the group responder, the owner's 🔐 and «typing». No console loops, no web server."""
        self.detect_topics_me()
        self.backlog_on_start()
        threading.Thread(target=self._loop, args=(self.responder_typing_tick, 4, "typing"), daemon=True).start()
        threading.Thread(target=self.sdk_watch, daemon=True).start()

    def detect_topics_me(self):
        try:
            self.chat_username = (self.tg.get_me() or {}).get("username")
        except TGError as e:
            log("getMe failed: %s" % e)
        self.responder_replay()

    def start_console(self):
        try:
            self.tg.set_commands()
        except TGError as e:
            log("setMyCommands failed: %s" % e)
        self.detect_topics()
        log("topics mode: %s" % ("yes" if self.topics_enabled else "no"))
        self.backlog_on_start()
        try:
            self.remove_old_tracker()
        except TGError as e:
            log("remove_old_tracker failed: %s" % e)
        self.greet_missing()
        for fn, period, name in ((self.forward_tick, 3, "forward"), (self.status_tick, 2, "status"),
                                 (self.notify_tick, 7, "notify"), (self.tmux.cleanup, 60, "cleanup"),
                                 (self.topics_tick, 5, "topics"), (self.panel_tick, 15, "panel"), (self.names_tick, 20, "names"),
                                 (self.chains_scan, 600, "chains"),
                                 (self.topic_cleanup, 600, "topic_cleanup"), (self.temp_tick, 5, "temp"),
                                 (self.webapp_tick, 10, "webapp"), (self.outbox_tick, 3, "outbox"),
                                 (self.inbox_tick, 3600, "inbox"), (self.stopfail_tick, 10, "stopfail"),
                                 (self.responder_typing_tick, 4, "typing")):
            threading.Thread(target=self._loop, args=(fn, period, name), daemon=True).start()
        threading.Thread(target=self.herdr_watch, daemon=True).start()
        threading.Thread(target=self.sdk_watch, daemon=True).start()
        if self.tg_chat is not self.tg:
            threading.Thread(target=self.chat_poll_loop, daemon=True).start()
        try:
            self.webapp_server = WebServer(self, "127.0.0.1", self.cfg.webapp_port, self.cfg.webapp_static)
            self.webapp_server.start()
            log("webapp: 127.0.0.1:%d" % self.webapp_server.port)
        except OSError as e:
            log("webapp server failed: %s" % e)

    def run_forever(self):
        if not self.cfg.allowed:
            log("WARNING: the allowed-users list is empty, all updates are ignored")
        (self.start_responder if self.cfg.role == "responder" else self.start_console)()
        log("zidos-pager started")
        while not self.stop_event.is_set():
            try:
                self.poll_once()
            except TGError as e:
                log("poll error: %s" % e)
                self.stop_event.wait(5)
            except Exception as e:
                log("poll crash: %s" % type(e).__name__)
                self.stop_event.wait(5)


MAX_BODY = 64 * 1024
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".mjs": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".jpg": "image/jpeg", ".webp": "image/webp", ".woff2": "font/woff2", ".ico": "image/x-icon",
                 ".webmanifest": "application/manifest+json"}


class BoundedHTTPServer(ThreadingHTTPServer):
    """At most max_handlers concurrent handlers; beyond the limit — an immediate 503."""
    request_queue_size = 64
    daemon_threads = True

    def __init__(self, addr, handler, max_handlers=32):
        self._slots = threading.BoundedSemaphore(max_handlers)
        super().__init__(addr, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 5\r\nContent-Length: 0\r\n"
                                b"Connection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


_ASSET_REF = re.compile(rb'((?:href|src)="/app/)([\w./-]+\.(?:css|js))"')


def version_assets(html, root):
    """/app/x.css -> /app/x.css?v=<mtime>: a proxy that caches static files for hours (Cloudflare: 4 h whatever the
    origin says) never pairs a new page with an old stylesheet."""
    def ver(m):
        try:
            v = int(os.path.getmtime(os.path.join(root, m.group(2).decode())))
        except OSError:
            return m.group(0)
        return m.group(1) + m.group(2) + b"?v=%d\"" % v
    return _ASSET_REF.sub(ver, html)


class WebServer:
    """Mini-app HTTP server: static files without auth, /api/* by Telegram initData or a device token."""

    def __init__(self, bot, host="127.0.0.1", port=18795, static_dir=None, io_timeout=30, max_handlers=32):
        self.bot = bot
        self.static_dir = static_dir or bot.cfg.webapp_static
        self._csp_cache = {}
        handler = self._handler()
        handler.timeout = io_timeout  # socket with a timeout: slow clients do not hold a thread forever
        self.httpd = BoundedHTTPServer((host, port), handler, max_handlers)
        self.port = self.httpd.server_address[1]

    def csp_for(self, path, data):
        """CSP for HTML: sha256 hashes of inline <script> (cached by file mtime and size)."""
        try:
            st = os.stat(path)
            key = (st.st_mtime, st.st_size)
        except OSError:
            key = None
        hit = self._csp_cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
        hashes_ = []
        for m in re.finditer(rb"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", data, re.S | re.I):
            hashes_.append("'sha256-%s'" % base64.b64encode(hashlib.sha256(m.group(1)).digest()).decode())
        csp = ("default-src 'self'; script-src 'self' https://telegram.org %s; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
               "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; base-uri 'none'; "
               "form-action 'self'" % " ".join(hashes_)).replace("telegram.org ;", "telegram.org;")
        self._csp_cache[path] = (key, csp)
        return csp

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "zidos-pager"
            command = path = ""  # a connection that times out before its first line has neither; log_error reads them

            def log_message(self, fmt, *args):  # no bodies, no initData and no signed links
                path = urllib.parse.urlsplit(self.path).path
                log("webapp %s %s" % (self.command, "/f/<hidden>" if path.startswith("/f/") else path))

            def _common(self):
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if not getattr(self, "_body_read", True):  # the request body was not fully read — close the connection
                    self.send_header("Connection", "close")
                    self.close_connection = True

            def _json(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self._common()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def _auth(self):
                bot = srv.bot
                self._device = None
                if (bot.cfg.webapp_dev and self.client_address[0] == "127.0.0.1"
                        and self.headers.get("X-Dev") == "1"
                        and not any(self.headers.get(h) for h in ("Cf-Connecting-Ip", "X-Forwarded-For", "Cf-Ray"))
                        and bot.cfg.allowed):
                    return sorted(bot.cfg.allowed)[0]
                user = verify_init_data(self.headers.get("X-Tg-Init-Data", ""), bot.cfg.token, time.time())
                if user and user["id"] in bot.cfg.allowed:
                    return user["id"]
                tok = self.headers.get("X-Device-Token")
                if tok:
                    res = bot.device_auth(tok)
                    if res:
                        self._device = res[1]
                        return res[0]
                return None

            def _static(self, path):
                rel = urllib.parse.unquote(path[len("/app"):]).lstrip("/") or "index.html"
                if "\0" in rel or ".." in rel.split("/"):
                    return self._json(404, {"error": "not found"})
                root = os.path.realpath(srv.static_dir)
                target = os.path.realpath(os.path.join(root, rel))
                if not target.startswith(root + os.sep) or not os.path.isfile(target):
                    return self._json(404, {"error": "not found"})
                try:
                    with open(target, "rb") as f:
                        data = f.read()
                except OSError:
                    return self._json(404, {"error": "not found"})
                ext = os.path.splitext(target)[1].lower()
                if ext in (".html", ".htm"):
                    data = version_assets(data, root)
                ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(target)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                if rel == "sw.js":
                    self.send_header("Service-Worker-Allowed", "/app/")
                if ext in (".html", ".htm"):
                    self.send_header("Content-Security-Policy", srv.csp_for(target, data))
                self._common()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def _download(self, token):
                path = srv.bot.file_for_token(token)
                if not path:
                    return self._json(403, {"error": "forbidden"})
                try:
                    f = open(path, "rb")
                    size = os.fstat(f.fileno()).st_size
                except OSError:
                    return self._json(404, {"error": "not found"})
                with f:
                    name = os.path.basename(path)
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition",
                                     "attachment; filename*=UTF-8''" + urllib.parse.quote(name))
                    self.send_header("Cache-Control", "no-store")
                    self._common()
                    self.end_headers()
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

            def _handle(self, method):
                self._body_read = method != "POST"
                path = urllib.parse.urlsplit(self.path).path
                if method == "GET" and (path == "/app" or path.startswith("/app/")):
                    return self._static(path)
                if method == "GET" and path == "/favicon.ico":  # browsers ask for it from the site root
                    return self._static("/app/favicon.ico")
                if method == "GET" and path.startswith("/r/"):  # a day report: the token is the access, no initData
                    if path.endswith(".json"):
                        data = srv.bot.report_json(path[3:-5])
                        if data is None:
                            return self._json(404, {"error": "not found"})
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-store")
                        self._common()
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    return self._static("/app/report.html")
                if method == "GET" and path.startswith("/f/"):  # a signed, expiring link: no initData
                    return self._download(path[3:])
                if not path.startswith("/api/"):
                    return self._json(404, {"error": "not found"})
                if method == "POST" and path == "/api/login":  # no auth: sign-in by a one-time token
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        length = -1
                    if not 0 <= length <= MAX_BODY:
                        self.close_connection = True
                        return self._json(413 if length > MAX_BODY else 400, {"error": "bad length"})
                    raw_login = self.rfile.read(length) if length else b""
                    self._body_read = True
                    try:
                        data = json.loads(raw_login.decode("utf-8")) if raw_login else {}
                    except ValueError:
                        data = None
                    if not isinstance(data, dict):
                        return self._json(400, {"error": "bad json"})
                    ip = self.headers.get("Cf-Connecting-Ip") or self.client_address[0]
                    code, obj = srv.bot.api_login(ip, str(data.get("token") or ""), self.headers.get("User-Agent", ""))
                    return self._json(code, obj)
                uid = self._auth()  # before any work with the store
                if uid is None:
                    return self._json(401, {"error": "auth"})
                body = {}
                if method == "GET" and path == "/api/file":
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    code, obj = srv.bot.api_file(uid, (q.get("path") or [""])[0])
                    return self._json(code, obj)
                if method == "POST":
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        length = -1
                    upload = re.fullmatch(r"/api/session/[^/]+/upload", path) is not None
                    voice = path == "/api/voice"
                    if length < 0:
                        return self._json(400, {"error": "bad length"})
                    if length > (UPLOAD_MAX if upload else VOICE_MAX if voice else MAX_BODY):
                        self.close_connection = True
                        if voice:
                            return self._json(413, {"ok": False, "code": "too_large", "error": "too large"})
                        return self._json(413, {"error": "too large"})
                    raw = self.rfile.read(length) if length else b""
                    self._body_read = True
                    if upload or voice:
                        body = {"content_type": self.headers.get("Content-Type", ""), "raw": raw}
                        raw = b""
                    try:
                        body = body or (json.loads(raw.decode("utf-8")) if raw else {})
                    except ValueError:
                        return self._json(400, {"error": "bad json"})
                    if not isinstance(body, dict):
                        return self._json(400, {"error": "bad json"})
                try:
                    code, obj = srv.bot.api_dispatch(uid, method, path, body, device=self._device)
                except Exception as e:
                    log("webapp error: %s" % type(e).__name__)
                    code, obj = 500, {"error": "internal"}
                self._json(code, obj)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_HEAD(self):  # the mini-app's update check; only static files answer HEAD
                path = urllib.parse.urlsplit(self.path).path
                if path == "/app" or path.startswith("/app/"):
                    self._body_read = True
                    return self._static(path)
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

        return H


REPORT_KEEP = 7 * 86400  # s: a report link works this long
_REPORT_WORD = re.compile(r"\s*отч[её]т\s*", re.I)
_REPORT_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")


def report_area(paths):
    """Where a commit changed things: the site (what users see) first, then forecasts and data, the server, the rest."""
    ps = [x for x in paths if not x.startswith(("journal/", "tests/", "docs/"))] or paths
    if any(x.startswith(("src/", "public/")) and not x.startswith("src/data/") or x.endswith((".css", ".html")) for x in ps):
        return "site"
    if any(x.startswith(("predictions/", "ratings/", "data/", "src/data/", "scripts/")) for x in ps):
        return "data"
    if any(x.startswith("server/") for x in ps):
        return "server"
    return "other"


def parse_report_log(out):
    """git log --format=%x1e%h%x1f%ct%x1f%an%x1f%s --name-only -> commits, newest first."""
    res = []
    for chunk in (out or "").split("\x1e")[1:]:
        head, _, rest = chunk.partition("\n")
        f = head.split("\x1f")
        if len(f) != 4:
            continue
        files = [x for x in rest.split("\n") if x.strip()]
        res.append({"h": f[0], "ts": int(f[1]) if f[1].isdigit() else 0, "author": f[2], "subject": f[3],
                    "files": files, "area": report_area(files)})
    return res


def relay_html(text, name, uid):
    """The message names its addressee with a mention (Telegram notifies them): the leading name, or a first line."""
    tag = '<a href="tg://user?id=%d">%s</a>' % (uid, esc(name))
    return tag + esc(text[len(name):]) if text.startswith(name) else tag + "\n" + esc(text)


def relay_main(cfg, argv):
    """zidos_pager.py relay --to NAME FILE [--reply MID]: a message to the shared chat on the owner's behalf. The
    addressee is required and mentioned; their id comes from the responder log (they wrote to the bot before).
    Answers until --until (ISO, default 3 days) are collected in relay.jsonl, not answered by the responder."""
    import argparse
    ap = argparse.ArgumentParser(prog="zidos_pager.py relay")
    ap.add_argument("--to", required=True)
    ap.add_argument("--reply", type=int)
    ap.add_argument("--until")
    ap.add_argument("file")
    a = ap.parse_args(argv)
    if not (cfg.responder_token and cfg.responder_chat is not None):
        print("relay: CC_RESPONDER_TOKEN / CC_RESPONDER_CHAT are not set", file=sys.stderr)
        return 2
    uid = None
    try:
        with open(cfg.responder_log, encoding="utf-8") as f:
            for ln in f:
                d = json.loads(ln)
                if d.get("who") == a.to and isinstance(d.get("from"), int):
                    uid = d["from"]
    except (OSError, ValueError):
        pass
    if uid is None:
        print("relay: %s has not written to the bot yet, the id is unknown" % a.to, file=sys.stderr)
        return 2
    with open(a.file, encoding="utf-8") as f:
        text = f.read().strip()
    tg = TG(cfg.responder_token, cfg.api_base)
    r = tg.send_one(cfg.responder_chat, relay_html(text, a.to, uid), reply_to=a.reply, parse_mode="HTML")
    until = datetime.fromisoformat(a.until).timestamp() if a.until else time.time() + 3 * 86400
    path = os.path.join(os.path.dirname(cfg.responder_log), "relay.json")
    try:  # earlier messages still waiting for answers keep being watched
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        mids = (old.get("mids") or []) if time.time() < old.get("until", 0) else []
        until = max(until, old.get("until", 0)) if mids else until
    except (OSError, ValueError, AttributeError):
        mids = []
    mids.append(r.get("message_id"))  # replies to this message are the answers (not to the one it replies to)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mids": [x for x in mids if x], "until": until, "to": a.to}, f)
    print("relay: sent, message %s" % r.get("message_id"))
    return 0


def main():
    migrate_legacy_paths(os.environ.get("HOME", os.path.expanduser("~")))
    cfg = Config()
    set_lang(cfg.lang)
    if sys.argv[1:2] == ["relay"]:
        return relay_main(cfg, sys.argv[2:])
    if sys.argv[1:2] == ["bots"]:
        return bots_main(cfg, sys.argv[2:])
    if not cfg.token:
        print(_t("Error: TG_BOT_TOKEN is not set (file ~/.config/zidos-pager/env or "
                 "ZIDOS_PAGER_ENV)."),
              file=sys.stderr)
        return 2
    if not cfg.allowed:
        print(_t("Error: TG_ALLOWED_USERS is not set (comma-separated Telegram user ids allowed to use the bot)."),
              file=sys.stderr)
        return 2
    try:
        Bot(cfg).run_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
