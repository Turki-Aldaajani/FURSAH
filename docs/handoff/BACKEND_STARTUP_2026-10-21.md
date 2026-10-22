# تشغيل الـ Backend: سجل 21 أكتوبر 2026

## ما نُفّذ فعليًا (كله قراءة أو فحص، دون تثبيت أو تعديل)

| الأمر/الفحص | النتيجة |
|---|---|
| قراءة `main.py` و`routes.py` و`schemas.py` و`dependencies.py` و`errors.py` و`config.py` و`data_package.py` و`repository.py` و`workflow.py` و`agent.py` و`tools.py` و`validation.py` و`backend/README.md` و`frontend/src/api/client.ts` | تم (انظر مسار الطلب أدناه) |
| بحث عن أي `site-packages/fastapi` في ملف المستخدم و`C:\SAAI` و`C:\SAAI Project` ومجلدات Python | **0 نتيجة**: لا توجد بيئة Backend على الجهاز |
| `backend/.venv` و`backend/.env` | غير موجودين |
| منفذ 8000 | لا شيء يستمع (الواجهة فقط على 5173، العملية 19032) |
| `uv pip install --dry-run --python C:\SAAI\.venv\Scripts\python.exe --only-binary :all: -r requirements.txt` | **فشل:** `pydantic-core==2.46.5 has no usable wheels` |
| PyPI: ملفات pydantic-core 2.46.5 | حزم Windows: cp39 وcp310 وcp311 وcp312 وcp313 وcp314 وcp314t فقط، ولا cp315. يوجد sdist لكنه يحتاج Rust |
| `rustc` و`cargo` | غير موجودين |
| `uv python list --only-downloads` | CPython 3.13.16 و3.12.15 متاحان للتنزيل عبر uv (لم يُنزَّل شيء) |

**السبب المباشر:** المفسّر الوحيد على الجهاز Python 3.15.0، و`requirements.txt` يثبّت `pydantic==2.13.5` الذي يحتاج `pydantic-core==2.46.5`، ولا توجد منه حزمة جاهزة لـ 3.15، ولا يوجد Rust لبنائها.

## مسار الطلب كما يعرّفه الكود

```
frontend (client.ts, VITE_API_BASE_URL افتراضيًا http://localhost:8000)
  POST /api/v1/discover  ->  routes.discover_route
      يتحقق pydantic من DiscoveryRequest (وإلا 422 FURSAH_REQUEST_VALIDATION_ERROR)
      deps = get_app_dependencies()  (lru_cache: Settings + DatasetLoader + OpportunityRepository + model_config)
      deps.evaluation_time = settings.resolve_evaluation_time()  (مرة لكل طلب)
      result = await workflow.discover(body, deps)       <- NotImplementedError -> 503 FURSAH_NOT_IMPLEMENTED
      FursahError -> رمزها وحالتها؛ أي استثناء آخر -> 500 FURSAH_INTERNAL_ERROR
  GET /api/v1/opportunities/{id} -> deps.repository.get_details(id)  (None -> 404؛ NotImplementedError -> 503)
  GET /health -> 200 دائمًا
  GET /ready  -> deps.readiness(): repository.is_ready() و workflow.application_is_ready(deps)
                 200 فقط إن أعاد الاثنان True حرفيًا؛ وإلا 503 مع checks/details
```

`agent.py` و`tools.py` و`validation.py` لا يستدعيها أي مسار حاليًا؛ `workflow.discover` هو نقطة الدمج الوحيدة، وهو حر في استدعائها أو لا.

**قيود العقد المهمة للمسار الأول:**
- `ok` يتطلب 1 إلى 3 توصيات بلا `missing_fields`.
- كل توصية: `opportunity_id` و`title` غير فارغين، و`eligibility_status` (`confirmed` أو `uncertain`)، و`reason`، و`source_ids` غير فارغة.
- الواجهة تتحقق أيضًا من أن `caveats` قائمة نصوص.

## النتائج المتوقعة بعد التشغيل (من الكود و`backend/README.md`)

`/health` 200 · `/ready` 503 (`storage` و`application` false: «Intentionally unfinished») · discover صحيح 503 `FURSAH_NOT_IMPLEMENTED` · discover غير صحيح 422 · details 503.

## أقصر مسار لطلب واحد (بعد التشغيل، دون نموذج لغوي)

1. **`repository.py`:**
   - تحميل `data/prepared/source_preparation.json` مرة واحدة.
   - تجميع المصادر بالمفتاح (الناشر + العنوان + الرابط)، وهو متحقَّق: 900 فرصة.
   - النوع من `classify_type`.
   - `is_ready()` تعيد True إن حُمّل سجل واحد على الأقل.
   - `search()` فلترة بسيطة، و`get_details()` تحويل إلى `OpportunityDetailsResponse`.
2. **`workflow.py`:**
   - `application_is_ready` تعيد جاهزية المخزن (لا نموذج بعد).
   - `discover` حتمية: فلترة بالنوع وطريقة التقديم والمدينة والميزانية (الرسوم المجهولة لا تمر كمجانية) والموعد عند `deps.evaluation_time`، ثم تطابق كلمات الطلب مع العنوان.
   - الناتج أفضل 3 بحالة `ok`، أو `no_match`.
   - `eligibility_status` دائمًا `uncertain`، و`reason` من نص المصدر، و`source_ids` كاملة.
3. **نقطة الدمج لاحقًا (DeepSeek):** داخل `discover` بين الاسترجاع والتحقق. `agent.run_agent` لتفسير الطلب أو اختيار المرشحين وشرحهم، و`validation.validate_result` يرفض أي معرّف أو اقتباس غير موجود في المرشحين.

**ملاحظة تقنية:** `opportunity_type_rules.py` موجود في `data/` وليس على مسار استيراد الـ backend؛ يلزم قرار بنقله أو نسخه إلى `backend/app/` أو إضافة المسار.

---

## تنفيذ الخيار «أ» (21 أكتوبر 2026، بعد موافقة المستخدم)

| الخطوة | الأمر | النتيجة |
|---|---|---|
| تنزيل Python | `uv python install 3.12 --no-bin` | CPython 3.12.15 في `%APPDATA%\uv\python\` (مُدار للمستخدم، بلا اختصارات PATH)؛ 3.15 لم يتغير |
| البيئة | `uv venv --python 3.12 backend/.venv` | `backend\.venv\Scripts\python.exe` = 3.12.15 (يتجاهلها git) |
| الاعتماديات | `uv pip install --python backend/.venv/Scripts/python.exe -r requirements.txt` | 23 حزمة؛ الإصدارات الستة المثبّتة مطابقة حرفيًا (pydantic-core 2.46.5) |
| الإعدادات | `cp -n .env.example .env` | أُنشئ `backend/.env` مطابقًا للقالب (يتجاهله git). `FURSAH_EVALUATION_TIME` فارغ = الوقت الفعلي |
| التشغيل | من `backend/`: `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000` | `Application startup complete` |

| الاختبار | الفعلي | المتوقع (README/الكود) |
|---|---|---|
| `GET /health` | 200 `{"status":"ok","service":"fursah-api"}` | 200 ✓ |
| `GET /ready` | 503، `storage:false` و`application:false` و«Intentionally unfinished» | 503 ✓ |
| `POST /api/v1/discover` صحيح | 503 `FURSAH_NOT_IMPLEMENTED` | 503 ✓ |
| `POST /api/v1/discover` غير صحيح | 422 `FURSAH_REQUEST_VALIDATION_ERROR` (query فارغ، profile ناقص) | 422 ✓ |
| `GET /api/v1/opportunities/SRC-0006` | 503 `FURSAH_NOT_IMPLEMENTED` | 503 ✓ |
| CORS preflight من `http://localhost:5173` | 200 مع `access-control-allow-origin: http://localhost:5173` | مسموح ✓ |

لم يتغير أي ملف متتبَّع في المستودع (`git diff` فارغ).

---

## أول مسار بحث فعلي (21 أكتوبر 2026)

**الملفات المعدّلة:** `backend/app/repository.py`، `backend/app/workflow.py`. **الملف الجديد:** `backend/tests/test_first_path.py`. لم يتغير: `routes.py` و`schemas.py` و`main.py` و`dependencies.py` والبيانات الخام وناتج التحضير و`opportunity_type_rules.py`.

**التصميم المنفّذ:**
- **المخزن:** `data/prepared/source_preparation.json`.
- **التجميع:** بالمفتاح (الرابط، العنوان، الناشر) ← 900 فرصة. المعرّف `opp-<sha1[:12]>`، و`get_details` تقبل أيضًا معرّف أي مصدر في الفرصة.
- **قاعدة التصنيف:** تُحمَّل من `data/opportunity_type_rules.py` عبر `importlib` (نسخة واحدة، بلا نسخ).
- **دمج الحقائق:** تتفق المصادر ← قيمة واحدة؛ تحديث صريح («تحديث من») ← يغلب في الحقول التي يذكرها؛ تعارض ← `null` مع `data_warnings`.
- **الموعد:** نفس سياسة `review_deadlines` (هامش يوم حول تاريخ الرياض)، ويُحسب عند وقت الطلب لا وقت التحضير. تُقرأ صيغة التمديد «إلى».
- **الاستبعاد:** الملغى (3 صيغ) والمغلق («باب التقديم مغلق»، «لاكتمال المقاعد») والمنتهي الموعد.
- **ما يبقى مع ملاحظة:** الرسوم المجهولة (لا تُعد مجانية) والموعد المجهول أو القريب.
- **الأهلية:** `uncertain` دائمًا، والشروط الإلزامية تظهر في `caveats`.
- **تفسير نص الطلب:** النوع والمدينة وطريقة التقديم والميزانية وكلمات الموضوع، ويُدمج مع `constraints`. التعارض بلا `constraint_resolution` ← `needs_info` + `constraint_conflict`.
- **ترتيب النتائج:** مطابقة الكلمات، ثم قلة الملاحظات، ثم أقرب موعد. تُرجع أفضل 3.
- `application_is_ready` ← جاهزية المخزن. `discover` محدودة بـ `workflow_timeout_seconds`.

**الاختبارات:**
- `backend/tests` (9، وقت التقييم مثبّت على 18 أكتوبر): **OK**.
- `data/test_opportunity_type_rules.py` (11): **OK**.
- HTTP حي (وقت فعلي 21 أكتوبر):
  - `/health` 200، و`/ready` **200** (`storage` و`application` true).
  - discover «أبحث عن ورشة» ← ok، 3 ورش.
  - discover تطوع بميزانية 0 ← ok، ومصدر بلا رسوم مع ملاحظة.
  - discover «الحوسبة الكمومية» ← no_match.
  - discover «ورشة في تحليل البيانات بالرياض» ← no_match، وهو صحيح بالدليل (الورشة الوحيدة عن بُعد انتهت في 9 أكتوبر والبقية في مدن أخرى).
  - discover تعارض المدينة ← needs_info.
  - discover غير صحيح ← 422.
  - details SRC-0004 ← الرسوم null مع تحذير.
  - details SRC-0006 ← الفرصة بمصدريها.
  - details SRC-9999 ← 404.

**تنبيه أدوات:** `curl -d '<عربي>'` من Git Bash على Windows يفسد النص العربي في الطلب. أرسل الجسم من ملف UTF-8 (`--data-binary @file.json`). المتصفح لا يتأثر.

---

## اختبار المسار من الواجهة (21 أكتوبر 2026)

**أدوات المتصفح غير متاحة في هذه الجلسة** (لم يُثبَّت امتداد Chrome)، فلم يُختبر العرض المرئي. البديل المنفّذ: تشغيل **كود عميل الواجهة نفسه** (`frontend/src/api/client.ts` و`profile/profile.ts`، دون تعديل) في Node 24 عبر esbuild الموجود في `frontend/node_modules`، ضد الخادم الحي، بالعنوان الافتراضي `http://localhost:8000` (لا يوجد `frontend/.env`).

```bash
cd frontend
node_modules/.bin/esbuild "C:/SAAI Project/handoff/scripts/frontend_client_e2e.ts" --bundle --platform=node --format=esm --define:import.meta.env.VITE_API_BASE_URL=undefined --outfile=<scratch>/e2e.mjs
node <scratch>/e2e.mjs
```

**النتائج** (الملف الشخصي الفارغ كما يرسله أول زائر):

| الحالة | النتيجة | تحقق الواجهة (`validDiscovery` و`validDetails`) |
|---|---|---|
| «أريد ورشة عمل في الرياض» | ok: SRC-0332 وSRC-0551 (الرياض، حضوري) وSRC-0009 (عن بعد) | مقبول؛ العربية سليمة |
| تفاصيل أول نتيجة (`getOpportunity`) | ok، الرسوم 50 | مقبول (المعرّف المطلوب = المعرّف المرجَع) |
| «ورشة في الحوسبة الكمومية للمريخ» | no_match | مقبول |
| «ورشة في جدة» مع تفضيل الرياض | needs_info مع `constraint_conflict` | مقبول |
| نفسه مع `constraint_resolution: "query"` | ok (جدة + عن بعد) | مقبول |
| طلب فارغ | خطأ تحقق محلي في الواجهة، لا يُرسل | — |
| CORS على POST فعلي | `access-control-allow-origin` لـ `http://localhost:5173` و`http://127.0.0.1:5173` | — |

**ملاحظة على العقد:** `getOpportunity` في الواجهة يرفض الاستجابة إن اختلف `opportunity_id` المرجَع عن المطلوب. الواجهة تطلب دائمًا بمعرّف الفرصة، فلا مشكلة. لكن الاسم البديل بمعرّف المصدر (SRC-xxxx) صالح لـ `/docs` و`curl` فقط، لا لرابط في الواجهة.

**لم يُختبر:** العرض المرئي في المتصفح (البطاقات والملاحظات والأزرار ورسالة needs_info). يحتاج خطوة يدوية.

---

## إصلاحات البحث (21 أكتوبر 2026)

**نقطة الرجوع (قبل التعديل، دون git commit):** `handoff\restore_points\2026-10-21_before_search_fixes\` (انظر `RESTORE.md` فيه: نسخة 7 ملفات + بصمات + رقعة `git diff`).

**المعدَّل:** `backend/app/repository.py` و`backend/app/workflow.py`. **الجديد:** `backend/tests/test_search_fixes.py`. لم يتغير: `schemas.py` و`routes.py` و`main.py` و`infrastructure/` والواجهة والبيانات وقاعدة التصنيف.

| الإصلاح | ما تغيّر |
|---|---|
| «أو» بدائل | `QueryReading.either_city_or_mode` و`SearchCriteria.either_city_or_mode`: يمر النشاط إن طابق المدينة **أو** طريقة التقديم. يُكتشف النمط «مدينة أو عن بعد» وعكسه (مع حرف جر اختياري). يظهر في `interpreted_filters` بسطر «المكان (بدائل)». «أو» بين قيمتين من البعد نفسه (مدينتان، نوعان) كانت بدائل أصلًا |
| كلمات النوع | بعد كشف النوع وطريقة التقديم والمدينة تُحذف الكلمات الحاملة لها كاملة قبل استخراج الكلمات المفتاحية، فلا يبقى «عملي» من «تدريب عملي» |
| أسماء التقنيات | `TECH_TERMS`: بايثون/Python، إكسل/Excel، اس كيو ال/SQL، باور بي آي/Power BI ← الاسم اللاتيني الموجود في العناوين. تُعالج قبل تجريد البادئات (كانت «بايثون» تتحول إلى «ايثون») |
| تطابق الموضوع | كلمات قوالب العناوين (df ≥ 15% من العناوين المميزة، وهي 9 كلمات عند 16.7% مقابل 9.7% لأكثر كلمة موضوع شيوعًا) لا تُعد دليلًا. إن وُجدت كلمات موضوع تظهر في عناوين فلا بد من تطابق أحدها في **العنوان**. كلمات لا تظهر في أي عنوان تتطلب تطابقًا في العنوان أو النص. كلمات المستوى («للمبتدئين») تُرتّب فقط |
| الملف الشخصي | ترتيب فقط، بعد درجة الطلب: مدينة الملف (+2) ومهارة في العنوان أو مطلوبة في الإعلان (+2) واهتمام أو تخصص في العنوان (+1). يظهر في `reason` عبارة «يتوافق مع ملفك: …» |

**الاختبارات:** `backend/tests` = **23 ناجحًا** (9 + 14 جديدة)، و`data` = 11 ناجحًا. وفحص عقد الواجهة (خادم مؤقت على 8001، ثم أُغلق) بكود عميل الواجهة الحقيقي: كل الحالات قُبلت. السكربت: `handoff\scripts\frontend_client_e2e.ts` (النسخة الأولى)؛ ونسخة الإصلاحات في المجلد المؤقت.

**الحالات الأربع (وقت التقييم 18 أكتوبر):**
- «فرص صحية في أبها أو عن بُعد» ← ok، بدائل؛ النتائج أنشطة عن بُعد لأنه لا توجد فرصة صحية مفتوحة في أبها أصلًا.
- «ورشة في تحليل البيانات في أبها أو عن بعد» ← SRC-0954 (Excel، أبها، مدمجة) كانت تُستبعد.
- «أبغى تدريب عملي في الدمام» ← 3 تدريبات في الدمام.
- «دورة بايثون للمبتدئين» ← SRC-0971 وSRC-0428 وSRC-0807 فقط.

**الخادم الحي:** النافذة التي شغّلتَها بـ`run_backend.bat` تعمل بالكود **القديم** (لا تعيد التحميل تلقائيًا). أغلقها وشغّل الملف مجددًا ليأخذ الكود الجديد.

---

## ربط النموذج (OpenRouter / DeepSeek) (21 أكتوبر 2026)

**نقطة الرجوع (قبل التعديل، دون `.env`):** `handoff\restore_points\2026-10-21_before_model_integration\`.

**ما وُجد قبل التنفيذ:** المتغيرات المعتمدة `FURSAH_MODEL_{PROVIDER,NAME,API_KEY,BASE_URL}` مع `FURSAH_MODEL_TIMEOUT_SECONDS=40` و`FURSAH_MODEL_MAX_RETRIES=1`. `FURSAH_MODEL_PROVIDER` نص حر بلا قائمة قيم، ولا ذكر لـ`openrouter` في الكود، ولا عميل نموذج منفَّذ (`agent.py` يرفع `NotImplementedError`). `FURSAH_FRAMEWORK_TRACK` وصف اختياري لا يقرؤه أي كود، وتُرك فارغًا.

**الإعدادات في `backend/.env` (متجاهَل من git):** `FURSAH_MODEL_PROVIDER=openrouter` و`FURSAH_MODEL_BASE_URL=https://openrouter.ai/api/v1` و`FURSAH_MODEL_NAME=deepseek/deepseek-v4-flash` (من كتالوج OpenRouter الحي: يدعم `response_format` و`structured_outputs`، سياق 1,048,576، 0.09$ إدخال و0.18$ إخراج لكل مليون رمز). **المفتاح فارغ**؛ يضعه المستخدم بنفسه في `FURSAH_MODEL_API_KEY`. `FURSAH_EVALUATION_TIME` فارغ.

**التنفيذ:** `backend/app/agent.py` (عميل httpx بروتوكول OpenAI-compatible، بلا حزم جديدة) و`backend/app/workflow.py` (استدعاء قبل البحث).
- **الدور:** يقرأ النموذج نص الطلب فقط (مع قائمة المدن) ويقترح مرشحات: نوع ومدينة وطريقة تقديم وميزانية وموضوع.
- **القبول بتحقق الكود لا النموذج:**
  - كل مرشح مصحوب باقتباس حرفي من الطلب.
  - القيم من قوائم مغلقة.
  - الخطة لا تحذف أي قيد وجدته القواعد الأساسية ولا تغيّر الميزانية.
  - «أو» تُقبل فقط إن وردت الكلمة في الطلب.
- **الإضافة لا الاستبدال:** الخطة المقبولة تُضاف إلى التفسير الأساسي ولا تنقص منه.
- **عند الفشل:** (خطأ مزوّد، أو مهلة، أو JSON غير صالح، أو خطة مرفوضة) يكمل البحث بالقواعد الأساسية، ويظهر في `interpreted_filters` سطر «تفسير الطلب: القواعد الأساسية (تعذّر اعتماد تفسير النموذج)»، ويُسجَّل في التتبع `model_interpretation` برمز الخطأ.
- **الحدود:** إعادة المحاولة مرة عند المهلة أو 429 أو 5xx فقط (`FURSAH_MODEL_MAX_RETRIES`)، لا عند 400/401/403/404. الخطوة كلها، بمحاولاتها، محدودة بـ`FURSAH_MODEL_TIMEOUT_SECONDS`، فلا تستهلك مهلة الـ90 ثانية. العنوان يجب أن يكون https (أو http على loopback) وبلا بيانات اعتماد. لم يُستخدم `FURSAH_MODEL_OUTPUT_RETRIES`.
- **الخصوصية:** لا يُرسل الملف الشخصي ولا نصوص الإعلانات؛ ولا يُطبع المفتاح في أي استجابة أو تتبع (اختبار).
- **الجاهزية:** `/ready` لم يتغير (المخزن فقط)، لأن النموذج إضافة مع بديل أساسي ولا يُستدعى في الفحص.

**الاختبارات:** `backend/tests` = **39 ناجحًا** (23 سابقة + 16 جديدة في `test_model_integration.py`)، و`data` = 11. لا اختبار آلي يتصل بشبكة (httpx.MockTransport ومفتاح وهمي)، وأُفرغ المفتاح داخل ملفات الاختبار فلا تستدعي الاختبارات الحالية النموذج حتى لو وُضع المفتاح في `.env`.

**اختبار شبكة حقيقي بلا تكلفة:** طلب فعلي إلى `https://openrouter.ai/api/v1/chat/completions` بمفتاح **وهمي** ← `HTTP 401` خلال 0.7 ثانية، عولج دون إعادة محاولة (`FURSAH_MODEL_ERROR`).

**لم يُختبر:** استدعاء ناجح بمفتاح حقيقي (المفتاح غير موجود في `.env` ولا في متغيرات العملية). للاختبار بعد وضعه: `cd backend` ثم `.venv\Scripts\python.exe "C:\SAAI Project\handoff\scripts\check_model_connection.py"` (طلب واحد قصير، لا يطبع المفتاح).


---

## تشخيص `FURSAH_NETWORK_ERROR` وإصلاحه (21 أكتوبر 2026)

انظر القسم 12 من `HANDOFF_FURSAH.md`. الخلاصة: `content: null` من نموذج استدلال استنفد `max_tokens` ← `AttributeError` ← 500 بلا CORS ← يظهر في الواجهة خطأ اتصال. الإصلاح في `agent.py` (معالجة المحتوى الفارغ + تعطيل التفكير لـOpenRouter) مع اختبارين. 41 اختبارًا ناجحًا؛ 0 خطأ في 32 طلبًا حقيقيًا بعده.
