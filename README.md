![SAAI — تعلم الذكاء الاصطناعي بالتطبيق](https://i.imgur.com/YHRhgqv.png)

**English** | [العربية](README.ar.md)

# FURSAH — فرصة

## حالة هذا المستودع (2026-10-22)

تطبيق بحث عن الفرص مبني على حزمة SAAI (1,000 إعلان عربي خيالي). المنفّذ هنا: إعداد البيانات وتصنيف الأنواع (`data/`)، وبحث حتمي مع تفسير اختياري للطلب بنموذج عبر OpenRouter (`backend/app/`)، وتتبّع JSONL وOpenTelemetry، ومقيّمات Day 4 (`backend/evals/`). تفاصيل كل مرحلة في [`docs/handoff/`](docs/handoff/)، والحالة النهائية لـ Day 4 في [`docs/handoff/DAY4_FINAL_STATUS.md`](docs/handoff/DAY4_FINAL_STATUS.md).

**آخر نتائج متحقق منها:** 105 اختبارات للخلفية و11 لقواعد الأنواع ناجحة؛ التقييم الرسمي على الحالات الـ24: **16 PASS / 8 FAIL**؛ حالة الفريق `TEAM-01` (`needs_info`) ناجحة. حالة Day 4: **COMPLETE WITH LIMITATIONS**.

### التشغيل (من `backend/`، Python 3.12)

```bash
python -m venv .venv            # ثم فعّل البيئة
python -m pip install -r requirements.txt
cp .env.example .env            # ضع مفتاح النموذج في .env فقط، ولا تضعه في Git
python -m uvicorn app.main:app --reload --port 8000
```

الواجهة من `frontend/`: `npm ci` ثم `cp .env.example .env` ثم `npm run dev` (انظر [frontend/README.md](frontend/README.md)). بدون مفتاح نموذج يعمل البحث بالقواعد الحتمية وحدها.

### الاختبارات والتقييم (من `backend/`)

```bash
python -m unittest discover -s tests                        # 105 اختبارات
python -m pip install arize-phoenix-otel==0.17.1 opentelemetry-sdk==1.44.0   # لازم لمشغّل التقييم فقط (بلا خادم Phoenix)
python -m evals.run_eval --label <name>                     # الحالات المشتركة الـ24
python -m evals.run_eval --cases evals/team_cases.jsonl     # حالة الفريق
```

النتائج تُحفظ في `backend/evals/results/` (مستبعدة من Git). اضبط `FURSAH_EVALUATION_TIME=2026-10-18T12:00:00+03:00` في `.env` لتطابق الواجهة وقت التقييم. ومع تفعيل النموذج تتفاوت بعض الدرجات بين التشغيلات.

### الملفات الرئيسية

| المسار | المحتوى |
|---|---|
| `backend/app/repository.py` | تحميل البيانات المجهزة وتجميع المصادر والفلاتر والترتيب |
| `backend/app/workflow.py` | تفسير الطلب والدمج مع القيود والـspans |
| `backend/app/agent.py` | عميل النموذج والتحقق من خطته |
| `backend/app/instrumentation.py`، `infrastructure/tracing.py` | أحداث JSONL مع حجب الخصوصية |
| `backend/evals/graders.py`، `team_cases.jsonl` | مقيّمات Day 4 وحالة الفريق (`run_eval.py` و`dev_cases.jsonl` من المنظمين) |
| `data/opportunity_type_rules.py`، `data/prepared/` | قواعد تصنيف الأنواع والبيانات المجهزة التي يقرؤها الخادم |
| `day4/` | أوراق عمل Day 4 |

### القيود المعروفة

- Phoenix غير مثبت، فالـspans تُجمع داخل العملية ولا تُعرض في واجهة Phoenix.
- إخفاقات متبقية في التقييم: نتائج خارج الموضوع (DEV-09، DEV-21)، و«لم يفتح بعد» (DEV-08)، و«العدول عن الإلغاء» (DEV-02)، ونقص في الاستدعاء والترتيب (DEV-04، DEV-10، DEV-16)، وتعارض وسوم النوع مع النص في DEV-07 (يُرفع للمنظمين).

### مواصلة العمل

اعمل محليًا، وشغّل الاختبارات والتقييم، ثم `git add` للملفات المقصودة و`git commit`، ثم `git push`. لا ترفع `.env` ولا `.venv` ولا `backend/evals/results/`، فـ `.gitignore` يستبعدها.

---

Build an opportunity-search application using your Day 1 design and the frameworks and tools you selected on Day 2.

SAAI supplies **1,000 raw fictional announcements, a finished frontend and a working API shell**. You implement preparation, internal schemas, storage, model clients, prompts, retrieval, tools, orchestration and business rules. The starter does not return pretend recommendations.

## Supplied versus student-owned

| Supplied integration boundary | Student decisions |
|---|---|
| Frontend and its HTTP request/response shapes | Internal records, database schema and storage technology |
| Endpoint URLs and structured errors | Model provider, authentication, framework or direct SDK |
| API startup and request validation | Prompts, extraction and retrieval |
| Request-scoped evaluation time | Tools, orchestration, ranking and eligibility rules |
| Optional raw CSV/JSON loading helpers | How prepared information is stored and accessed |

Keep the public API contract compatible with the provided frontend. **This does not prescribe a database schema or agent architecture.** Internal examples and learner modules may be merged, split, renamed or replaced; reconnect their imports and the API entry points when doing so.

## Follow this path

| Step | What you do | Visible milestone |
|---|---|---|
| 1. Start | Follow the [backend](backend/README.md) and [frontend](frontend/README.md) setup instructions. | Interface opens; `/health` is 200. Unfinished search is expected. |
| 2. Understand | Follow the [data guide](data/README.md) and notebook on ten announcements. | You can explain each source, missing facts and related announcements. |
| 3. Prepare and store | Extend the sample with the facts your application needs; choose your storage. | Your saved records retain their source IDs and original evidence. |
| 4. Connect details | Implement storage access and map one actual record to the details contract. | `/api/v1/opportunities/{id}` returns that record. |
| 5. Connect discovery | Retrieve candidates, handle constraints and return a supported outcome. | One real search works through the supplied frontend. |
| 6. Expand | Apply your preparation to all 1,000 announcements and review difficult cases. | Your complete application handles unknowns, updates and conflicts. |

This is a learning sequence, not a required agent architecture. The small sample is a milestone, not a reduced final requirement.

### Install in the right environment

- **API:** install `backend/requirements.txt` in your Python environment.
- **Notebook:** select the intended Python kernel, install Jupyter if needed, and use the notebook's package cell for pandas/IPython.
- **Frontend:** install Node.js with npm, then run `npm ci` in `frontend/`.
- **Your implementation:** add only the model, storage or framework libraries you choose. Starting the API shell does not install or implement an AI framework.

### Understand the workload

The nine exercises teach source preparation; they do not complete a search engine. Your first application milestone is one source-backed details response from the sample, followed by one working frontend search. Then expand to the full corpus. Use the [field-mapping worksheet](data/README.md#mapping) before implementation. You do not need to populate every optional field to complete the first working path.

## Project map

```text
fursah-starter/
├── README.md
├── data/
│   ├── README.md
│   ├── data_exploration_and_preparation.ipynb          # English guided exercises
│   ├── data_exploration_and_preparation.ar.ipynb       # Arabic guided exercises
│   ├── preparation_helpers.py    # Supplied notebook helpers / أدوات الدفتر
│   └── raw/raw_opportunities.csv  # Original evidence; keep unchanged
├── backend/
│   ├── README.md
│   ├── requirements.txt          # API shell dependencies only
│   ├── .env.example
│   └── app/
│       ├── main.py / routes.py   # Supplied HTTP plumbing
│       ├── schemas.py           # Public HTTP contract
│       ├── internal.py          # Optional, replaceable internal type examples
│       ├── repository.py        # Suggested storage access location
│       ├── agent.py             # Suggested model/client and agent location
│       ├── tools.py             # Suggested tool location
│       ├── validation.py        # Suggested business-rule location
│       ├── workflow.py          # Default discovery integration entry point
│       └── infrastructure/      # Configuration, errors, tracing, optional file helpers
└── frontend/                     # Provided React interface
```

These learner files describe responsibilities, not a required execution order. A team's application may use a different organization.

## What works before implementation?

Start the backend and frontend using their guides. `/health` returns HTTP 200. `/ready` returns HTTP 503 with unfinished storage/application checks. Valid discovery and details requests return HTTP 503 with `FURSAH_NOT_IMPLEMENTED`. Invalid requests return HTTP 422. These are intentional starter states, not a broken installation.

## Evaluation time

The classroom uses **18 October 2026 at 12:00 Asia/Riyadh**. Set this in your backend environment using the [time policy](backend/README.md#evaluation-time-policy) to match the notebook exercise. Outside the exercise, actual request time remains the default. Keep source dates unchanged.

## What counts as a completed exercise?

- Prepare the full raw dataset using your own reproducible process and storage design.
- Connect your chosen implementation to the public discovery/details contract.
- Return source-supported results, missing-information requests, no matches or controlled errors as appropriate.
- Preserve uncertainty, source provenance and conflicting facts; do not invent eligibility confirmation.
- Make readiness reflect your actual components and configuration.
- Demonstrate the end-to-end application and explain your design decisions.

Use the backend guide’s API examples and `/docs` to understand the interface. Testing and comprehensive evaluation belong to the later testing day.
