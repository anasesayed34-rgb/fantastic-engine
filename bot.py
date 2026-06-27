"""
PT SGU PDF Bot - @PhysicalTherapyDatabot
بوت لتخزين وتنظيم ملفات وريكوردات كلية العلاج الطبيعي - جامعة الصالحية الجديدة

نسخة موسّعة: إدارة مشرفين، تتبع دخول/خروج المستخدمين، سجل أنشطة كامل،
ترقيم صفحات، رفع/حذف بالأزرار، إحصائيات، بث رسائل، تصدير CSV.
"""

import csv
import io
import logging
import os
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db

# ============================================================
#  الإعدادات
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
DB_PATH = os.environ.get("DB_PATH", "pt_sgu.db")
LOG_PATH = os.environ.get("LOG_PATH", "bot_activity.log")

# المواد الافتراضية (تُستخدم فقط أول مرة لو جدول المواد فاضي)
DEFAULT_SUBJECTS = [
    "Anatomy",
    "Physiology",
    "Biophysics",
    "Biochemistry",
    "Kinesiology",
    "Manual Muscle Testing",
]

PAGE_SIZE = 6          # عدد الملفات في الصفحة الواحدة
USERS_PAGE_SIZE = 10
LOG_PAGE_SIZE = 10

# ============================================================
#  اللوجينج (تسجيل كل حاجة بتحصل في ملف + الشاشة)
# ============================================================

logger = logging.getLogger("pt_sgu_bot")
logger.setLevel(logging.INFO)

_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_console)

_file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_file_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)  # تقليل ضجيج مكتبة الشبكة

# حالات محادثة رفع الملف
UP_SUBJECT, UP_TITLE, UP_FILE, UP_MORE = range(4)
# حالات محادثة البث
BC_MESSAGE, BC_CONFIRM = range(4, 6)

CONTENT_ICONS = {
    "document": "📄",
    "audio": "🎙️",
    "video": "🎬",
    "photo": "🖼️",
}


# ============================================================
#  دوال مساعدة عامة
# ============================================================

def icon_for(content_type: str) -> str:
    return CONTENT_ICONS.get(content_type, "📄")


def is_owner(user_id: int) -> bool:
    """مالك أساسي (محدد من متغيرات البيئة) - صلاحيات كاملة، ما يقدر أي حد يشيله."""
    return user_id in OWNER_IDS


def is_admin(user_id: int) -> bool:
    """مالك أساسي أو مشرف تمت إضافته من البوت."""
    return is_owner(user_id) or db.is_admin_in_db(user_id)


def fmt_dt(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_str


def user_label(user_id: int, username: str | None, first_name: str | None) -> str:
    if username:
        return f"@{username}"
    if first_name:
        return f"{first_name} ({user_id})"
    return str(user_id)


async def log_and_record(update: Update, action: str, details: str = ""):
    user = update.effective_user
    if user:
        db.log_activity(user.id, user.username or user.first_name or "بدون اسم", action, details)
    logger.info("ACTION=%s USER=%s DETAILS=%s", action, user.id if user else "?", details)


def pagination_row(prefix: str, page: int, total_pages: int) -> list:
    if total_pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"{prefix}|{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"{prefix}|{page + 1}"))
    return row


# لوحات المفاتيح الثابتة (الأزرار اللي تحت شاشة الكتابة)
def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("📚 المواد"), KeyboardButton("🔍 البحث")],
        [KeyboardButton("ℹ️ مساعدة"), KeyboardButton("🆔 آيديي")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton("🛠 لوحة التحكم")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# ============================================================
#  أوامر عامة
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    await log_and_record(update, "start", "مستخدم جديد" if is_new else "عاد للبوت")

    text = (
        "👋 أهلاً بيك في بوت *PT SGU PDF*\n\n"
        "هنا تقدر تلاقي وتطلب ملفات، ملخصات، وريكوردات المحاضرات لكل مواد الفرقة.\n\n"
        "📚 *المواد* - تصفح الملفات بالمادة\n"
        "🔍 *البحث* - دور بكلمة من اسم الملف\n"
        "🆔 *آيديي* - تعرف الآيدي بتاعك (يفيد لو هتتعمل أدمن)\n"
        "ℹ️ *مساعدة* - كل الأوامر\n\n"
        "استخدم الأزرار تحت 👇 أو اكتب الأوامر مباشرة."
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=main_menu_keyboard(user.id)
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lines = [
        "📖 *الأوامر المتاحة*",
        "",
        "/materials - تصفح المواد والملفات",
        "/search كلمة - البحث عن ملف",
        "/myid - عرض آيدي حسابك",
        "/cancel - إلغاء أي عملية جارية",
    ]
    if is_admin(user_id):
        lines += [
            "",
            "🛠 *أوامر الأدمن*",
            "/upload - رفع ملف جديد",
            "/delete - حذف ملف (تصفح واختيار)",
            "/addsubject اسم - إضافة مادة",
            "/removesubject اسم - حذف مادة",
            "/stats - إحصائيات البوت",
            "/users - قائمة المستخدمين",
            "/broadcast - إرسال إشعار لكل المستخدمين",
            "/export - تصدير قائمة الملفات (CSV)",
            "/activity - آخر الأنشطة المسجّلة",
            "/admins - قائمة المشرفين",
        ]
        if is_owner(user_id):
            lines += [
                "/addadmin آيدي - تعيين مشرف جديد",
                "/removeadmin آيدي - إزالة مشرف",
            ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    role = "👑 مالك" if is_owner(user.id) else ("🛠 مشرف" if is_admin(user.id) else "👤 مستخدم")
    username_part = f"@{user.username}" if user.username else "—"
    await update.message.reply_text(
        f"🆔 آيديك: {user.id}\nاسم المستخدم: {username_part}\nصلاحيتك: {role}"
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لا توجد عملية جارية لإلغائها حاليًا.")


# ============================================================
#  تصفح المواد والملفات
# ============================================================

async def materials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subjects = db.get_subjects()
    if not subjects:
        await update.message.reply_text("لا توجد مواد مضافة لسه. الأدمن يقدر يضيف بـ /addsubject")
        return

    keyboard = []
    for subj in subjects:
        count = db.count_files_by_subject(subj)
        keyboard.append([InlineKeyboardButton(f"📘 {subj} ({count})", callback_data=f"subj|{subj}|0")])

    await update.message.reply_text(
        "📚 اختار المادة اللي عايزها:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def render_subject_files(query, subject: str, page: int):
    total = db.count_files_by_subject(subject)
    if total == 0:
        await query.edit_message_text(
            f"لا يوجد ملفات لمادة {subject} لسه 🙁",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع للمواد", callback_data="back_materials")]]),
        )
        return

    rows = db.get_files_by_subject(subject, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    keyboard = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(
            f"{icon_for(row['content_type'])} {row['title']}", callback_data=f"get|{row['id']}"
        )])

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    nav = pagination_row(f"subjpg|{subject}", page, total_pages)
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("⬅️ رجوع للمواد", callback_data="back_materials")])

    await query.edit_message_text(
        f"📚 ملفات {subject} ({total}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_subject_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, subject, page_str = query.data.split("|", 2)
    await render_subject_files(query, subject, int(page_str))


async def subject_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, subject, page_str = query.data.split("|", 2)
    await render_subject_files(query, subject, int(page_str))


async def back_to_materials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subjects = db.get_subjects()
    if not subjects:
        await query.edit_message_text("لا توجد مواد مضافة لسه.")
        return
    keyboard = []
    for subj in subjects:
        count = db.count_files_by_subject(subj)
        keyboard.append([InlineKeyboardButton(f"📘 {subj} ({count})", callback_data=f"subj|{subj}|0")])
    await query.edit_message_text("📚 اختار المادة اللي عايزها:", reply_markup=InlineKeyboardMarkup(keyboard))


async def send_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    file_pk = int(query.data.split("|", 1)[1])
    row = db.get_file_by_id(file_pk)

    if not row:
        await query.message.reply_text("الملف ده مش موجود (ممكن يكون اتمسح).")
        return

    caption = f"📌 {row['title']}\n📚 {row['subject']}"
    content_type = row["content_type"]
    file_id = row["file_id"]

    try:
        if content_type == "document":
            await query.message.reply_document(document=file_id, caption=caption)
        elif content_type == "audio":
            await query.message.reply_audio(audio=file_id, caption=caption)
        elif content_type == "video":
            await query.message.reply_video(video=file_id, caption=caption)
        elif content_type == "photo":
            await query.message.reply_photo(photo=file_id, caption=caption)
        else:
            await query.message.reply_text("نوع ملف غير معروف.")
            return
        db.increment_download(file_pk)
        await log_and_record(update, "download", f"file_id={file_pk} title={row['title']}")
    except TelegramError as e:
        logger.warning("فشل إرسال الملف %s: %s", file_pk, e)
        await query.message.reply_text("⚠️ حصل خطأ وإحنا بنحاول نبعت الملف. جرّب تاني كمان شوية.")


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم كذا: /search اسم الملف أو كلمة من العنوان")
        return
    keyword = " ".join(context.args)
    context.user_data["last_search"] = keyword
    await render_search_page(update.message, keyword, 0, edit=False)


async def render_search_page(target, keyword: str, page: int, edit: bool):
    total = db.count_search_results(keyword)
    if total == 0:
        await target.reply_text(f"مفيش نتائج لـ '{keyword}' 🙁")
        return

    rows = db.search_files(keyword, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    keyboard = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(
            f"{icon_for(row['content_type'])} {row['title']} ({row['subject']})",
            callback_data=f"get|{row['id']}",
        )])

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    nav = pagination_row("searchpg", page, total_pages)
    if nav:
        keyboard.append(nav)

    text = f"🔍 نتائج البحث عن '{keyword}' ({total}):"
    if edit:
        await target.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def search_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("|", 1)[1])
    keyword = context.user_data.get("last_search", "")
    if not keyword:
        await query.edit_message_text("انتهت صلاحية البحث ده، اكتب /search تاني.")
        return
    await render_search_page(query.message, keyword, page, edit=True)


async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ============================================================
#  رفع الملفات (أدمن فقط) — تدفّق محادثة
# ============================================================

async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ الأمر ده للأدمن بس.")
        return ConversationHandler.END

    subjects = db.get_subjects()
    if not subjects:
        await update.message.reply_text("لا توجد مواد. ضيف مادة الأول بـ /addsubject اسم_المادة")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(subj, callback_data=f"upsubj|{subj}")] for subj in subjects]
    await update.message.reply_text(
        "📤 اختار المادة اللي هترفع لها الملف:\n(يمكنك إلغاء العملية بـ /cancel في أي وقت)",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return UP_SUBJECT


async def upload_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split("|", 1)[1]
    context.user_data["upload_subject"] = subject
    await query.edit_message_text(f"تمام، المادة: {subject}\nدلوقتي اكتب عنوان الملف (مثلاً: محاضرة 3 - القلب):")
    return UP_TITLE


async def upload_title_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("اكتب عنوان صحيح من فضلك.")
        return UP_TITLE
    context.user_data["upload_title"] = title
    await update.message.reply_text("تمام 👌 دلوقتي بعت الملف (PDF / صورة / صوت / فيديو):")
    return UP_FILE


async def upload_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    subject = context.user_data.get("upload_subject")
    title = context.user_data.get("upload_title")

    if msg.document:
        content_type, f = "document", msg.document
        file_name = f.file_name or "file"
    elif msg.audio:
        content_type, f = "audio", msg.audio
        file_name = f.file_name or "audio.mp3"
    elif msg.voice:
        content_type, f = "audio", msg.voice
        file_name = "voice_note.ogg"
    elif msg.video:
        content_type, f = "video", msg.video
        file_name = f.file_name or "video.mp4"
    elif msg.photo:
        f = msg.photo[-1]
        content_type = "photo"
        file_name = "photo.jpg"
    else:
        await msg.reply_text("الرجاء إرسال ملف (PDF / صوت / فيديو / صورة).")
        return UP_FILE

    file_size = getattr(f, "file_size", None)
    db.add_file(subject, content_type, title, f.file_id, file_name, update.effective_user.id, file_size)
    await log_and_record(update, "upload", f"subject={subject} title={title}")

    keyboard = [[
        InlineKeyboardButton("➕ رفع ملف تاني (نفس المادة)", callback_data="moreup|yes"),
        InlineKeyboardButton("✅ خلاص", callback_data="moreup|no"),
    ]]
    await msg.reply_text(
        f"✅ تم الحفظ بنجاح!\n📚 المادة: {subject}\n📌 العنوان: {title}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return UP_MORE


async def upload_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.endswith("yes"):
        await query.edit_message_text(f"تمام، المادة لسه: {context.user_data.get('upload_subject')}\nاكتب عنوان الملف الجديد:")
        return UP_TITLE
    context.user_data.clear()
    await query.edit_message_text("تم. تقدر تستخدم /upload تاني وقت ما تحب.")
    return ConversationHandler.END


async def upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء عملية الرفع.")
    return ConversationHandler.END


# ============================================================
#  حذف الملفات (أدمن فقط) — تصفح واختيار بالأزرار
# ============================================================

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ الأمر ده للأدمن بس.")
        return

    # دعم الطريقة القديمة: /delete رقم_الملف مباشرة
    if context.args:
        try:
            file_pk = int(context.args[0])
        except ValueError:
            await update.message.reply_text("رقم غير صحيح.")
            return
        row = db.get_file_by_id(file_pk)
        if not row:
            await update.message.reply_text("الملف ده غير موجود.")
            return
        keyboard = [[
            InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"delyes|{file_pk}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="delno"),
        ]]
        await update.message.reply_text(
            f"هل تريد حذف:\n📌 {row['title']} ({row['subject']})؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    subjects = db.get_subjects()
    if not subjects:
        await update.message.reply_text("لا توجد مواد.")
        return
    keyboard = [[InlineKeyboardButton(f"📘 {s} ({db.count_files_by_subject(s)})", callback_data=f"delpick|{s}|0")] for s in subjects]
    await update.message.reply_text("🗑 اختار المادة اللي عايز تحذف منها ملف:", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_delete_subject_files(query, subject: str, page: int):
    total = db.count_files_by_subject(subject)
    if total == 0:
        await query.edit_message_text(f"لا يوجد ملفات لمادة {subject}.")
        return

    rows = db.get_files_by_subject(subject, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    keyboard = [[InlineKeyboardButton(
        f"🗑 {icon_for(r['content_type'])} {r['title']}", callback_data=f"delconfirm|{r['id']}"
    )] for r in rows]

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    nav = pagination_row(f"delpg|{subject}", page, total_pages)
    if nav:
        keyboard.append(nav)

    await query.edit_message_text(f"اختار الملف اللي عايز تحذفه من {subject}:", reply_markup=InlineKeyboardMarkup(keyboard))


async def delete_pick_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    _, subject, page_str = query.data.split("|", 2)
    await render_delete_subject_files(query, subject, int(page_str))


async def delete_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    _, subject,
