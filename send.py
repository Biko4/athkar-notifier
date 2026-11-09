"""مُرسِل تنبيهات «اذكر الله» — يعمل في GitHub Actions كل ~15 دقيقة.

1. يقرأ وثائق admin_notifications ذات status == 'pending'.
2. ما حان موعده (sendAt فارغ أو مضى) يُحجز بمعاملة (pending → sending)
   حتى لا يُرسل مرتين، ثم يُرسل عبر FCM إلى topic كل لغة: all_<lang>.
3. يضع status='sent' و sentAt، ثم يحدّث app_settings/notifications.lastSentAt
   ليعرف التطبيق بقراءة واحدة أن هناك جديداً في «رسائل التطبيق».

المتغيرات:
  FIREBASE_SERVICE_ACCOUNT  محتوى ملف مفتاح الخدمة JSON (من GitHub Secrets)
  DRY_RUN=true              تحقق من الرسائل عند FCM دون إرسال ودون تعديل Firestore
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import credentials, firestore, messaging
from google.cloud.firestore_v1.base_query import FieldFilter

# لغات التطبيق الحالية + المخطط لها (topic بلا مشتركين لا يضر)
LANGS = ["ar", "en", "ur", "bn", "hi", "zh", "tr", "id", "fr", "fa", "ms", "ru"]
TOPIC_PREFIX = "all_"                # يطابق AppConstants.fcmTopicPrefix
CHANNEL_ID = "admin_messages"        # يطابق NotificationService._adminChannelId
COL = "admin_notifications"
META_DOC = "app_settings/notifications"
MAX_LATE = timedelta(hours=48)       # أقدم من هذا (السكربت كان متوقفاً) → expired
STUCK_AFTER = timedelta(minutes=30)  # 'sending' عالق (انقطاع) → failed بدل إعادة الإرسال

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def init():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        sys.exit("FIREBASE_SERVICE_ACCOUNT غير موجود في Secrets")
    firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    return firestore.client()


def pick(m, lang):
    if isinstance(m, str):
        return m
    m = m or {}
    return m.get(lang) or m.get("ar") or m.get("en") or next(iter(m.values()), "")


def build_messages(doc_id, d):
    data = {"id": doc_id, "deepLink": d.get("deepLink") or ""}
    image = d.get("imageUrl") or None
    msgs = []
    for lang in LANGS:
        title, body = pick(d.get("title"), lang), pick(d.get("body"), lang)
        if not title and not body:
            continue
        msgs.append(messaging.Message(
            topic=TOPIC_PREFIX + lang,
            notification=messaging.Notification(title=title, body=body, image=image),
            data=data,
            android=messaging.AndroidConfig(
                priority="high",
                ttl=timedelta(days=3),
                notification=messaging.AndroidNotification(channel_id=CHANNEL_ID),
            ),
        ))
    return msgs


@firestore.transactional
def claim(transaction, ref):
    snap = ref.get(transaction=transaction)
    if not snap.exists or (snap.to_dict() or {}).get("status") != "pending":
        return False
    transaction.update(ref, {"status": "sending", "claimedAt": datetime.now(timezone.utc)})
    return True


def recover_stuck(db, now):
    for snap in db.collection(COL).where(filter=FieldFilter("status", "==", "sending")).stream():
        claimed = (snap.to_dict() or {}).get("claimedAt")
        if claimed and now - claimed > STUCK_AFTER:
            print(f"[{snap.id}] عالق في sending → failed")
            snap.reference.update({"status": "failed", "error": "انقطع الإرسال؛ أعد إنشاء الإشعار إن لم يصل"})


def main():
    db = init()
    now = datetime.now(timezone.utc)
    if not DRY_RUN:
        recover_stuck(db, now)

    last_sent = None
    had_error = False
    pending = db.collection(COL).where(filter=FieldFilter("status", "==", "pending")).stream()

    for snap in pending:
        d = snap.to_dict() or {}
        send_at = d.get("sendAt")
        if send_at and send_at > now:
            continue  # مجدول لاحقاً
        if send_at and now - send_at > MAX_LATE:
            print(f"[{snap.id}] تأخر أكثر من 48 ساعة → expired")
            if not DRY_RUN:
                snap.reference.update({"status": "expired"})
            continue

        msgs = build_messages(snap.id, d)
        if not msgs:
            print(f"[{snap.id}] بلا عنوان/نص → failed")
            if not DRY_RUN:
                snap.reference.update({"status": "failed", "error": "بلا عنوان أو نص"})
            continue

        if DRY_RUN:
            resp = messaging.send_each(msgs, dry_run=True)
            print(f"[{snap.id}] DRY_RUN: {resp.success_count} صالحة، {resp.failure_count} خطأ")
            continue

        if not claim(db.transaction(), snap.reference):
            continue  # أخذه تشغيل آخر

        try:
            resp = messaging.send_each(msgs)
            if resp.success_count == 0:
                errs = [str(r.exception) for r in resp.responses if r.exception]
                raise RuntimeError(errs[0] if errs else "فشل غير معروف")
            sent_at = datetime.now(timezone.utc)
            snap.reference.update({
                "status": "sent",
                "sentAt": sent_at,
                "sentCount": resp.success_count,
                "failCount": resp.failure_count,
            })
            last_sent = sent_at
            print(f"[{snap.id}] أُرسل لـ {resp.success_count} لغة")
        except Exception as e:  # noqa: BLE001
            had_error = True
            print(f"[{snap.id}] فشل: {e}")
            snap.reference.update({"status": "failed", "error": str(e)[:300]})

    if last_sent:
        db.document(META_DOC).set({"lastSentAt": last_sent}, merge=True)

    if had_error:
        sys.exit(1)  # يظهر التشغيل باللون الأحمر في Actions


if __name__ == "__main__":
    main()
