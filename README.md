# athkar-notifier

يرسل تنبيهات الأدمن في تطبيق «اذكر الله» عبر Firebase Cloud Messaging، مجاناً بدون خطة Blaze.
يعمل في GitHub Actions كل ~15 دقيقة ([.github/workflows/send.yml](.github/workflows/send.yml)).

## كيف يعمل
1. الأدمن ينشئ إشعاراً → وثيقة في `admin_notifications` بحالة `pending`.
2. [send.py](send.py) يرسل كل ما حان موعده إلى topic كل لغة (`all_ar`، `all_en`، …).
3. يضع `status: sent` و`sentAt`، ويحدّث `app_settings/notifications.lastSentAt`.
4. التطبيق مشترك في topic لغته فقط، ويعرض الرسالة أيضاً في قسم «رسائل التطبيق».

الحالات في لوحة الأدمن: `pending` ← `sending` ← `sent`، أو `failed` / `expired` (تأخر أكثر من 48 ساعة).

## الإعداد (مرة واحدة)
1. Firebase Console → مشروع `biko-athkar` → Project settings → **Service accounts** → **Generate new private key** (ملف JSON).
2. هذا المستودع → Settings → Secrets and variables → Actions → **New repository secret**:
   - الاسم: `FIREBASE_SERVICE_ACCOUNT`
   - القيمة: محتوى ملف JSON كاملاً.
3. احذف ملف JSON من جهازك بعد لصقه. **لا ترفعه أبداً** (`.gitignore` يمنع ملفات json).
4. تبويب Actions → `send-notifications` → **Run workflow** مع `dry_run=true` للتجربة.

## ملاحظات
- GitHub يوقف المهام المجدولة في المستودعات العامة بعد 60 يوماً بلا أي commit؛ يظهر تنبيه في تبويب Actions ويُعاد تفعيلها بزر **Enable workflow**.
- مواعيد GitHub قد تتأخر بضع دقائق في أوقات الضغط.
