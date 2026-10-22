# فحص توافق 2.3 — `run_eval.py` مع تطبيقنا (22 أكتوبر 2026)

فحص للقراءة فقط: لم يُعدَّل أي ملف، ولم تُثبَّت حزم، ولم يُشغَّل `run_eval.py` ولا أي شيفرة منزَّلة (قرأتُ النصوص وحلّلتُ البيانات وأجريتُ فحوصًا تجريبية `--dry-run` للحزم). أرقام الأسطر من الملفات المستعادة كما هي.

## 1. كيف يجمع `run_eval.py` الـ spans ويمررها
**المصدر الوحيد: OpenTelemetry داخل العملية نفسها، لا ملف JSONL ولا Phoenix.**

1. **قبل استيراد التطبيق** يضبط متغيرات البيئة (`run_eval.py:138-140`): `FURSAH_EVALUATION_TIME` من الحالات، و`FURSAH_PHOENIX_ENABLED=true` (قسرًا)، و`FURSAH_PHOENIX_PROJECT`.
2. يستورد (`142-147`): `opentelemetry.sdk.trace.export.SimpleSpanProcessor` و`InMemorySpanExporter` (من `opentelemetry-sdk`)، ثم `setup_observability` و`start_span` من `app/infrastructure/observability.py`.
3. يفحص Phoenix بطلب HTTP (`150-157`)؛ إن لم يُجب: `phoenix_up=False`. ثم يستدعي **دائمًا** `setup_observability(export=phoenix_up)` (`158-159`)، فتُسجَّل `TracerProvider` عامة.
4. يضيف معالج `SimpleSpanProcessor` بمُصدِّر **في الذاكرة** `InMemorySpanExporter` إلى هذه الـprovider (`160-165`): هذا ما تقرؤه الـgraders. (إن كان Phoenix حيًّا يبقى مصدّره بجانبه.)
5. يشغّل التطبيق **داخل العملية** عبر `httpx.ASGITransport` (`262-270`): نفس المسار والتحقق والتبعيات، بلا شبكة. وتُرسل كل حالة إلى `POST /api/v1/discover` بالجسم `{request_id: case_id, query, profile, constraints}` (`206`).
6. لكل حالة (`271-290`): `memory.clear()` ثم يفتح **span جذرًا اسمه `eval.case`** عبر `start_span` (`274-275`)، يشغّل الحالة، ثم يأخذ من الذاكرة **الـspans التي لها `trace_id` الجذر نفسه** مرتبةً بوقت البدء، ويحوّلها إلى `CaseSpans([(name, attributes)])` (`285-289`).
7. **`CaseSpans`** (`39-52`): قاموس `{اسم الـspan: خصائصه}` والأخير يغلب عند التكرار؛ و`spans.all(name)` تعيد كل الـspans بذلك الاسم؛ و`spans.all_spans()` كلها.
8. يستدعي كل grader بالتوقيع `grader(case, response, spans)` (`run_graders`، `77-97`): `case` سطر من `dev_cases.jsonl`، و`response` كائن `DiscoveryResponse` بعد التحقق، و`spans` الكائن أعلاه. `None` ← «لا ينطبق» وتُهمل. استثناء أو درجة خارج [0,1] أو بلا اسم مقياس ← `GRADER ERROR` (والخروج برمز 1).

**ما يعني هذا لمسارنا:** الـspans التي تراها الـgraders تأتي **فقط** مما ينشئه تطبيقنا عبر OpenTelemetry (`start_span`) أو أداة تجهيز إطار (instrumentor). ملفنا JSONL (`Tracer`) **لا يقرؤه `run_eval`** إطلاقًا (يكتم طباعته فقط، `209`).

## 2. هل يحتاج Phoenix أو exporter؟
| العنصر | هل يلزم لتشغيل التقييم؟ | الدليل |
|---|---|---|
| **خادم Phoenix** (`phoenix serve`) | **لا.** لحفظ الآثار وفتحها فقط | إن لم يُجب: «grading continues, but traces are not saved» (`156-157`)، و`export=False`، والتقييم يقرأ الذاكرة |
| **حزمة `opentelemetry-sdk`** | **نعم، ضرورية** | `run_eval.py:144-145` تستوردها قبل أي تقييم؛ وهي غير مثبتة عندنا (فقط `opentelemetry-api`) ← `ImportError` فوري |
| **حزمة `phoenix.otel` (`arize-phoenix-otel`)** | **نعم، ضرورية** حتى مع `export=False` | `setup_observability` تستورد `from phoenix.otel import HTTPSpanExporter, SimpleSpanProcessor, register` (`observability.py:60`) قبل فرع `export`، و`run_eval` يفرض `ENABLED=true` فتنهار بـ`RuntimeError` إن غابت الحزمة |
| مُجهّزات الأطر (`openinference-instrumentation-*`) | لا لنا | نستعمل `httpx` مباشرة؛ الوضع الصحيح `FURSAH_PHOENIX_INSTRUMENT=none` |

**فحص تجريبي للحزم (لم يُثبَّت شيء):** `arize-phoenix-otel==0.17.1` (كما في `requirements-observability.txt`) يضيف **17 حزمة** تشمل `opentelemetry-sdk==1.44.0` ومصدّرات OTLP و`wrapt` و`grpcio`، وتُحلّ مع إصداراتنا المثبتة دون تعارض، **ولا تسحب** `openai` ولا `pydantic-ai` ولا `langchain` (فلا يتغير ما يختاره `auto`). أما `requirements-observability.txt` كاملًا (خادم Phoenix) فهو **137 حزمة**. لم أتحقق من أن `arize-phoenix-otel` يصدّر الأسماء الثلاثة فعلًا (يلزم تثبيته).

## 3. مطابقة ما تقرؤه الـgraders مع ما نسجّله
**ما يقرؤه `graders.py` الحالي:** `case["expected"]["status"]` و`response.status.value` فقط (`grade_status`). والمساعد `tool_inputs` (`34-51`) يقرأ من أي span الخصائص `tool.name` و`input.value`. لا اسم span ثابتًا فيه؛ الأسماء من تعريفنا، وورقة 2.2 تقترح:

| ما تتوقعه ورقتا 2.2 و2.3 | ما نسجّله فعلًا |
|---|---|
| span جذري `workflow.discover` بخصائص `request_id` و`evaluation_time` | **لا span.** حدث JSONL `request_received` بحقل `evaluation_time` ومعرّف الطلب |
| `repo.search` بخصائص `criteria` و`candidate_ids` (معرّفات **مصادر**) | لا span. حدث JSONL `retrieval_completed` بـ`criteria` و`ranking[].opportunity_id` (معرّفات **فرص** لا مصادر، وأعلى 20) |
| `llm.generate` بخصائص `input.value` و`output.value` و`llm.token_count.*` | لا span. حدث JSONL `model_interpretation` بـ`usage` ورد خام بصمة فقط (متعمَّد، خصوصية) |
| `tool.<name>` مع `tool.name` و`input.value` | لا أدوات (لا حلقة وكيل)؛ البحث دالة عادية |
| استدعاء `setup_observability()` عند بدء التطبيق | **لا يُستدعى** في أي موضع من كودنا |

الفحص: لا `start_span` ولا `get_tracer` ولا `setup_observability` في كود التطبيق (باستثناء تعريفها). **النتيجة العملية:** في كل حالة سيحتوي `spans` على `eval.case` وحده. وبالتالي لا grader قائم على spans يعمل اليوم، لكن `grade_status` وأي grader يعتمد على الاستجابة والحالات يعمل بلا تغيير.

**الخلاصة بشأن 2.2:** ما نفّذناه (JSONL منظم) صحيح ومفيد، لكنه **لا يغطي ما تعدّه ورقة 2.2 تسليمها**: أثر OpenTelemetry واحد لكل بحث (جذر + LLM + ≥2 مخصصة). الفرق قائم بين عقد الورقة وتنفيذنا.

## 4. المقاييس: بالاستجابة والحالات وحدهما، وما يحتاج spans
**ممكنة الآن بلا أي تغيير في التطبيق** (من `response` و`case["expected"]`):
| المقياس | ما يقيسه | ملاحظة |
|---|---|---|
| `e2e.status` (معطى) | الحالة النهائية | قواعد الورقة: `no_match` الوحيد الصحيح حين `uncertain` فارغة، و`ok` الوحيد الصحيح حين توجد مقبولة |
| صحة التوصيات | كل توصية حسب `source_ids`: خطأ إن وُجد مصدر مستبعد أو خارج القوائم؛ صحيحة إن وُجد مقبول ولا مستبعد؛ غير مُقيَّمة إن كانت مصادرها كلها غير مؤكدة | الورقة تعرض اسمًا مثاليًا `e2e.recommendations` |
| الالتزام بالقيود | مصادر مستبعدة أُعيدت، مجمّعة **بسبب الاستبعاد** المسمّى في الحالة (`over_budget` و`location_not_preferred` و`delivery_not_requested` و`type_not_requested` و`outside_dates` و`deadline_passed` و`status_*`...) | الإشارة المرجعية موجودة في الحالات نفسها |
| استرجاع المقبولة | هل ظهرت المصادر المقبولة ضمن التوصيات المعادة | `None` في `no_match` |
| الترتيب | موضع أول توصية صحيحة (أو MRR) داخل الاستجابة | ترتيب الاستجابة موجود |
| اتساق العقد | تفرّد المصادر وعدم فراغها... | معظمه يفحصه `run_eval` أصلًا (`protocol`) ومُحقِّق `DiscoveryResponse` |

**تحتاج spans إضافية (تجهيز جديد بصيغة OpenTelemetry):**
- استرجاع المرشحين **قبل اقتطاع الثلاثة** (`repo.search.candidate_ids` على مستوى المصادر).
- صحة سبب استبعاد كل مصدر (لا حدثنا الحالي يعطيه لكل مصدر؛ العيّنة عشرة فقط).
- مقاييس خطوة النموذج (زمن، رموز، `finish_reason`) من `llm.generate`.
- `tool_inputs(...)`: غير منطبق (لا استدعاء أدوات).
- **لا يمكن بأي طريقة اليوم:** صحة تفسير النموذج للطلب، لأن الحالات بلا مرشحات متوقعة (التسمية على مستوى المصدر فقط).

## 5. هل كل توصية قابلة للتقييم بـ`source_ids`؟ (من بيانات الحالات وتجميعنا)
- **نظافة التسميات:** 24 حالة؛ **لا تداخل** بين `acceptable` و`excluded` و`uncertain` داخل أي حالة؛ و**كل** المعرّفات المتوقعة موجودة في بياناتنا؛ كل حالة `ok` فيها مقبول واحد على الأقل؛ كل `no_match` بلا `uncertain` (كما تقول الورقة).
- **الاتساق مع تجميعنا للمصادر:** كل توصية عندنا تحمل **كل مصادر الفرصة** (900 فرصة، منها 100 بمصدرين). فحصتُ هذه المجموعات مقابل التسميات في الحالات الـ24: **لا توجد مجموعة واحدة تخلط فئتين** (643 مجموعة مستبعدة بالكامل، 38 مقبولة بالكامل، 18 غير مؤكدة بالكامل، لا `A+E` ولا `A+U` ولا `U+N`). أي أن قاعدة «مصدر مستبعد واحد يفسد التوصية» **لا تضر بنا بسبب التجميع**، وأن الغموض في الورقة (مجموعة غير مؤكدة + خارج القوائم) لا يقع فعليًا.
- **سعة الإجابة:** في كل حالة `ok` يوجد ما لا يقل عن مجموعة «صحيحة» ممكنة (1 إلى 3). وعدد المصادر المقبولة قد يزيد على عدد المجموعات (مثلًا DEV-02: 5 مصادر ← 3 مجموعات لأن بعضها إعادة نشر)، فمقياس الاسترجاع يجب أن يُحسب **على مستوى المجموعة** (أي مصدر من التوصية)، وهو ما تقوله الورقة («any of»).
- **ملاحظة:** حالات `uncertain` لا تُخرَج من التوصيات بقاعدة الورقة، فلا عقوبة ولا مكافأة؛ ونظامنا قد يُرجعها مع تنبيه.

## 6. `FURSAH_EVALUATION_TIME`
- **كيف يمرَّر:** `run_eval` يضبط متغير **البيئة** `FURSAH_EVALUATION_TIME` من الحالات (`138`) **قبل** استيراد التطبيق، فيغلب أي قيمة في `backend/.env`. وفي تطبيقنا: `routes.discover_route` يقرأ `deps.settings.resolve_evaluation_time()` ويمرّر النتيجة إلى كل المنطق؛ و`repository.get_details` يستدعي `get_settings()` من جديد فيقرأ البيئة أيضًا؛ والموضع الوحيد الذي يقرأ الساعة الحقيقية هو الاحتياط في `config.py:58` عند غياب القيمة.
- **حالتنا:** `.env` فارغ، فيطبع `run_eval` ملاحظة فقط («Note: FURSAH_EVALUATION_TIME is blank...»). وكل الحالات الـ24 على اللحظة نفسها `2026-10-18T12:00:00+03:00` (شرط `run_eval`: لحظة واحدة).
- **ما يلزم للتقييم:** **لا شيء** في الكود ولا الإعدادات. وضبط القيمة في `.env` مطلوب فقط لتطابق الواجهة الحيّة مع التقييم (README Day 4 يقول: «Outside the exercise, leave it blank»)، وهو قرار منتج لا شرط للتشغيل.
- **أثران جانبيان عند التشغيل بإعداداتنا الحالية** (لا يغيّرهما `run_eval`):
  1. مفتاح النموذج في `.env` ← 24 استدعاءً حقيقيًا للنموذج (نتائج غير حتمية، تكلفة نحو 0.0005$).
  2. `FURSAH_TRACE_JSONL_PATH` في `.env` ← أحداث الحالات تُلحَق بسجلك الحقيقي `backend/traces/fursah_trace.jsonl` (`run_eval` يكتم الطباعة لا الكتابة في الملف).

## 7. الاتفاق مع قيود هذه المهمة
لم يُعدَّل `run_eval.py` ولا `dev_cases.jsonl` ولا إعدادات التتبع ولا منطق البحث؛ ولم تُكتب graders ولم تُثبَّت حزم.

## 8. التوصية: أقل تعديل لتشغيل baseline
**أ. الضروري لتشغيل baseline (بلا تعديل أي ملف في المشروع):**
1. **تثبيت 17 حزمة** في `backend/.venv`: `arize-phoenix-otel==0.17.1` وتوابعه (يشمل `opentelemetry-sdk==1.44.0`). بدونها ينهار `run_eval` عند الاستيراد. (قرارك: هذا تثبيت حزم لم يُنفَّذ.)
2. التشغيل من `backend/`: `python -m evals.run_eval --label baseline`، مع تفريغ `FURSAH_TRACE_JSONL_PATH` لهذه العملية حتى لا تلوّث سجلك، وقرار بشأن النموذج: **مفعّل** (يمثّل ما يراه المستخدم والتقييم المخفي، لكنه غير حتمي) أو **معطّل** بتفريغ `FURSAH_MODEL_API_KEY` (حتمي، مرجع). والأفضل تشغيل الاثنين وتسمية كل منهما.
3. **graders تعتمد على الاستجابة والحالات فقط** (القسم 4). تعمل بلا تعديل في التطبيق، ويعمل `grade_status` الحالي بلا أي span.

**ب. تحسينات (غير لازمة لـbaseline):**
1. **إكمال 2.2 بصيغة الورقة:** استدعاء `setup_observability()` عند بدء التطبيق، وspan جذري `workflow.discover`، وspan للنموذج `llm.generate`، وspan `repo.search` بـ`candidate_ids` **على مستوى المصادر**. (مطلوب لـgraders مبنية على spans ولتسليم 2.2 كما تحدده الورقة.)
2. تثبيت `requirements-observability.txt` كاملًا (137 حزمة) وتشغيل `phoenix serve` لحفظ الآثار وفتح الحالات الفاشلة، مع `FURSAH_PHOENIX_INSTRUMENT=none` صراحةً لأن عميلنا `httpx`.
3. ضبط `FURSAH_EVALUATION_TIME` في `.env` إن أردتَ تطابق الواجهة مع التقييم.

## ما لم أتحقق منه
- أن `arize-phoenix-otel` يصدّر `HTTPSpanExporter` و`SimpleSpanProcessor` و`register` (يلزم تثبيته).
- سلوك `run_eval` عمليًا (لم يُشغَّل).
- هل ستقبل الورقة أو المدرّب ملف JSONL بديلًا عن أثر Phoenix في تسليم 2.2.
