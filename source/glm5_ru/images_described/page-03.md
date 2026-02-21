# page-03.png

> **Контекст:** Figure 3: On LMArena, GLM-5 is the #1 open model in both Text Arena and Code Arena.
Figure 4: Results on several long-horizon tasks. Left: Vending-Bench 2; Right: CC-Bench-V2.
planning and resource ma

---

## Тип изображения
Скриншот / График

## Краткое описание
Изображение содержит два скриншота рейтинговых таблиц LMArena (Figure 3), демонстрирующих лидерство модели GLM-5 в категориях Text Arena и Code Arena, а также график и диаграмму (Figure 4) с результатами производительности на долгосрочных задачах: Vending-Bench 2 и CC-Bench-V2.

## Структура изображения
Верхняя часть: два параллельных скриншота рейтинговых таблиц с темным фоном, содержащих списки моделей с показателями рейтинга.
Нижняя часть: слева - линейный график с несколькими цветными кривыми, показывающий динамику баланса во времени; справа - горизонтальная столбчатая диаграмма сравнения трех моделей по нескольким метрикам.

## Текстовые элементы

| Оригинал (EN) | Перевод (RU) | Расположение |
|---|---|---|
| GLM-5 | GLM-5 | заголовок обоих скриншотов |
| Arena | Arena | заголовок обоих скриншотов |
| #1 open model | #1 открытая модель | выделение на обоих скриншотах |
| in Text Arena | в Text Arena | левый скриншот |
| in Code Arena | в Code Arena | правый скриншот |
| ranking #11 overall | рейтинг #11 в общем зачете | левый скриншот |
| ranking #6 overall | рейтинг #6 в общем зачете | правый скриншот |
| ARENA: AS | ARENA: AS | правый верхний угол левого скриншота |
| ARENA: AS/CODE | ARENA: AS/CODE | правый верхний угол правого скриншота |
| AGENTIC: WEBARENA | AGENTIC: WEBARENA | правый скриншот |
| Figure 3: On LMArena, GLM-5 is the #1 open model in both Text Arena and Code Arena. | Рисунок 3: На LMArena модель GLM-5 занимает 1-е место среди открытых моделей как в Text Arena, так и в Code Arena. | подпись под верхними скриншотами |
| Money Balance Over Time | Баланс денежных средств во времени | заголовок графика слева |
| CC-Bench-V2: GLM-4.7 vs. GLM-5 vs. Claude Opus 4.5 | CC-Bench-V2: GLM-4.7 против GLM-5 против Claude Opus 4.5 | заголовок диаграммы справа |
| GLM-4.7 | GLM-4.7 | легенда графика и диаграммы |
| GLM-5 | GLM-5 | легенда графика и диаграммы |
| Claude Opus 4.5 | Claude Opus 4.5 | легенда графика и диаграммы |
| Gemini 2 Pro | Gemini 2 Pro | легенда графика |
| Claude Opus 3.5 | Claude Opus 3.5 | легенда графика |
| GPT-4.5 | GPT-4.5 | легенда графика |
| o1-mini-O1 | o1-mini-O1 | легенда графика |
| Mistral-a2 | Mistral-a2 | легенда графика |
| Days in simulation | Дни в симуляции | ось X графика |
| Frontend | Фронтенд | метка на диаграмме |
| End-to-End Evaluation | Сквозная оценка | метка на диаграмме |
| End-to-end Evaluation | Сквозная оценка | метка на диаграмме |
| Long Horizon | Долгосрочные задачи | метка на диаграмме |
| Large Team Scenario | Сценарий большой команды | метка на диаграмме |
| Multi-Step Cross-Task | Многошаговые кросс-задачи | метка на диаграмме |
| 98.0% | 98,0% | значение на диаграмме |
| 76.0% | 76,0% | значение на диаграмме |
| 76.0% | 76,0% | значение на диаграмме |
| 26.0% | 26,0% | значение на диаграмме |
| 20.0% | 20,0% | значение на диаграмме |
| 65.4% | 65,4% | значение на диаграмме |
| 65.5% | 65,5% | значение на диаграмме |
| 55.0% | 55,0% | значение на диаграмме |
| 97.8% | 97,8% | значение на диаграмме |
| Figure 4: Results on several long-horizon tasks. Left: Vending-Bench 2; Right: CC-Bench-V2. | Рисунок 4: Результаты на нескольких долгосрочных задачах. Слева: Vending-Bench 2; Справа: CC-Bench-V2. | подпись под графиками |

**Текст абзаца под рисунками:**

| Оригинал (EN) | Перевод (RU) |
|---|---|
| planning and resource management. Figure 4 (right) further shows results on our internal evaluation suite CC-Bench-V2. GLM-5 significantly outperforms GLM-4.7 across frontend, backend, and long-horizon tasks, narrowing the gap with Claude Opus 4.5. | планирование и управление ресурсами. Рисунок 4 (справа) дополнительно показывает результаты нашего внутреннего набора оценок CC-Bench-V2. GLM-5 значительно превосходит GLM-4.7 по фронтенд-, бэкенд- и долгосрочным задачам, сокращая разрыв с Claude Opus 4.5. |

**Раздел Methods:**

| Оригинал (EN) | Перевод (RU) |
|---|---|
| Methods. | Методы. |
| Figure 5 shows the overall training pipeline of GLM-5. Our Base Model training began with a massive 27 trillion token corpus, prioritizing code and reasoning early on. We then employed a distinct Mid-training phase to progressively extend context length from 4K to 200K, focusing specifically on long-context agentic data to ensure stability in complex workflows. In Post-Training, we moved beyond standard SFT. We implemented a sequential Reinforcement Learning pipeline—starting with Reasoning RL, followed by Agentic RL, and finishing with General RL. Crucially, we utilized On-Policy Cross-Stage Distillation throughout this process to prevent catastrophic forgetting, ensuring the model retains its sharp reasoning edge while becoming a robust generalist. In summary, the leap in GLM-5's performance is driven by the following technical contributions: | Рисунок 5 показывает общий конвейер обучения GLM-5. Обучение нашей базовой модели началось с масштабного корпуса в 27 триллионов токенов, с ранним приоритетом на код и рассуждения. Затем мы применили отдельную фазу промежуточного обучения для постепенного расширения длины контекста с 4K до 200K, специально фокусируясь на долгоконтекстных агентных данных для обеспечения стабильности в сложных рабочих процессах. На этапе пост-обучения мы вышли за рамки стандартного SFT. Мы внедрили последовательный конвейер обучения с подкреплением — начиная с RL для рассуждений, затем агентное RL и завершая общим RL. Критически важно, что мы использовали межэтапную дистилляцию на основе политики на протяжении всего процесса для предотвращения катастрофического забывания, гарантируя, что модель сохраняет острое преимущество в рассуждениях, становясь при этом надежным универсалом. Таким образом, скачок в производительности GLM-5 обусловлен следующими техническими вкладами: |
| First, we adopt DSA (DeepSeek Sparse Attention) [9], a novel architectural innovation that significantly reduces both training and inference costs. While GLM-4.5 improved efficiency through a standard MoE architecture, DSA allows GLM-5 to dynamically allocate attention resources based on token importance, drastically lowering the computational overhead without compromising long-context understanding or reasoning depth. With DSA, we scale the model parameters up to 744B and extend the training token budget to 28.5T tokens. | Во-первых, мы применяем DSA (DeepSeek Sparse Attention) [9], новаторское архитектурное решение, которое значительно снижает затраты как на обучение, так и на вывод. В то время как GLM-4.5 повысила эффективность благодаря стандартной MoE-архитектуре, DSA позволяет GLM-5 динамически распределять ресурсы внимания на основе важности токенов, радикально снижая вычислительные накладные расходы без ущерба для понимания длинного контекста или глубины рассуждений. С помощью DSA мы масштабируем параметры модели до 744B и расширяем бюджет обучающих токенов до 28,5T токенов. |
| Second, we have engineered a new asynchronous reinforcement learning infrastructure. Building on the "slime" framework and the decoupled rollout engines initialized in GLM-4.5, our new infrastructure further decouples generation from training to maximize GPU utilization. This system allows for massive-scale exploration of agent trajectories without the synchronization bottlenecks that previously hampered iteration speed, significantly improving the efficiency of our RL post-training pipeline. | Во-вторых, мы разработали новую асинхронную инфраструктуру обучения с подкреплением. Основываясь на фреймворке "slime" и разделенных движках развертывания, инициализированных в GLM-4.5, наша новая инфраструктура дополнительно отделяет генерацию от обучения для максимизации использования GPU. Эта система позволяет проводить масштабное исследование траекторий агентов без узких мест синхронизации, которые ранее замедляли скорость итераций, значительно повышая эффективность нашего конвейера пост-обучения с RL. |

**Номер страницы:** 3 (внизу страницы)

## Перевод для alt-текста
Скриншоты рейтингов LMArena, где GLM-5 занимает первое место среди открытых моделей в Text Arena и Code Arena, а также графики результатов на долгосрочных задачах Vending-Bench 2 и CC-Bench-V2, демонстрирующие превосходство GLM-5 над GLM-4.7.