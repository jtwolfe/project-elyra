# AI, Python & training — open reading notes

Started: 2026-07-29
Sources: Wikipedia primarily; official Python/ML docs where useful.
Scope: ~10 min open-ended pass for recall fuel (not a textbook).

---

## What "machine learning" is

- **ML**: statistical algorithms that **learn from data** and **generalize to unseen data**, without being fully hand-programmed for every case.
- Tom Mitchell (operational def): learn from experience **E** w.r.t. tasks **T** and performance **P** if performance on T (measured by P) improves with E.
- Nested boxes people use casually: **AI ⊃ ML ⊃ deep learning** (DL = neural nets with many layers / representation learning).
- Foundations: statistics + mathematical optimization. Related: data mining (often more EDA / unsupervised exploration).
- Theoretical frame often cited: **PAC learning** (probably approximately correct); much of classical ML/DL as **empirical risk minimization** (fit training loss as a proxy for true risk).

### Paradigms (high level)

| Paradigm | Signal | Typical goal |
|----------|--------|----------------|
| **Supervised** | Labeled pairs (x → y) | Classification, regression |
| **Unsupervised** | Unlabeled x | Clusters, density, compression, structure |
| **Reinforcement** | Rewards/penalties via actions in an environment | Policies that maximize return |
| **Self-/semi-supervised** (modern LLMs lean here) | Labels from the data itself (e.g. next-token) or mixed | Scale representation learning |

Also: anomaly detection, structured prediction, dimensionality reduction, human-in-the-loop / active learning.

History spine worth keeping: Samuel 1959 "machine learning"; Hebb 1949 (connection strength from co-activity); McCulloch–Pitts; 1980s **backprop** revival (Rumelhart/Hinton/Williams et al.); 2010s deep learning scale-up; GANs (Goodfellow 2014); AlphaGo-style RL (2016).

---

## The training loop (neural nets)

Core loop almost every modern net training run is a variant of:

1. **Forward pass** — feed input x through layers (weights W, activations f) → prediction ŷ = g(x; W).
2. **Loss / cost** — scalar C(y, ŷ) measuring "how wrong" (e.g. cross-entropy for class probs, squared error for regression).
3. **Backward pass (backpropagation)** — efficient reverse-mode autodiff / chain rule: compute ∇_W C without redoing redundant partials layer-by-layer from output → input.
4. **Parameter update** — step weights opposite the gradient (or via a smarter optimizer built on that gradient).

Notation sketch (feedforward):

- g(x) := f_L(W_L f_{L-1}(W_{L-1} … f_1(W_1 x) …))
- Training set: {(x_i, y_i)}; per-example loss C(y_i, g(x_i))
- At **eval** time: weights fixed, inputs vary; network ends at outputs.
- At **train** time: example fixed, **weights** vary; graph ends at the **loss**.

Backprop computes ∂C/∂w efficiently; the **optimizer** decides how to use that gradient (plain SGD, momentum, Adam, …). People often say "backprop" for the whole train step — strictly it is the gradient algorithm, not the update rule.

### Gradient descent → SGD → mini-batches

- **Gradient descent**: x_{n+1} = x_n − η ∇f(x_n). Walk downhill on the loss surface. η = learning rate (too small → slow; too large → overshoot/diverge).
- **Batch GD**: gradient over **entire** dataset each step — exact but expensive at scale.
- **SGD**: approximate ∇Q(w) with one example (or a **mini-batch**). Cheaper steps, noisier path; noise can help escape sharp bad minima; vectorized mini-batches win in practice (GPU-friendly).
- Objective often a sum/average: Q(w) = (1/n) Σ Q_i(w) — empirical risk. Classic stats link: M-estimation, least squares, MLE.
- Variants you will see everywhere: **momentum**, **AdaGrad**, **RMSProp**, **Adam** (adaptive per-parameter step sizes / moments).

### Activations & losses (practical defaults)

- Hidden acts: historically sigmoid/tanh; modern default often **ReLU** (and cousins: GELU, Swish, …).
- Output: sigmoid (binary), **softmax** (multi-class probs).
- Losses: **cross-entropy / log loss** (classification), **MSE / squared error** (regression). Loss + last activation should match the problem (e.g. logits + CE, not softmax-then-MSE as a default habit).

---

## What information / data training needs

### Supervised recipe (classic checklist)

1. Decide what a **training sample** is (char vs word vs image vs full document…).
2. **Gather a representative** training set with inputs **and** correct outputs (experts, measurements, logs).
3. Choose **feature representation** (or let DL learn features from raw pixels/tokens). Curse of dimensionality: too many weak features hurts.
4. Choose **model family + learning algorithm** (+ hyperparameters).
5. Train; tune on a **validation** split (or cross-validation).
6. Report final numbers on a held-out **test** set — never the train set alone.

### Issues that decide "how much / what quality" data you need

- **Bias–variance tradeoff**: flexible models fit hard functions but overfit small data; stiff models underfit complex truth.
- **Function complexity vs n**: simple targets → small n can suffice; complex interactions → need lots of data + flexible models.
- **Input dimensionality**: extra irrelevant dimensions inflate variance; feature selection / dimensionality reduction helps.
- **Label noise**: wrong y's → do not memorize; prefer higher bias / regularization / cleaner labels. "Deterministic noise" = unmodelable part of the target acting like corruption.
- **i.i.d. assumption** (often violated): train distribution should match deploy distribution or you get silent failure modes (domain shift).

### Splits and leakage

- **Train / val / test** (or train / test + CV). Test is sacred for final estimate.
- **Leakage**: accidental signal from the future / from labels / from duplicate near-copies across splits — makes metrics lie.

### What modern large models add (preview; deepen in later pass)

- **Scale**: web-scale text/code/images; quality filtering, dedup, toxicity/PII handling matter as much as raw GB.
- **Tokenization**: text → integer token ids (BPE/WordPiece/Unigram). The model never sees "characters" raw in the classic LM setup — it sees tokens.
- **Self-supervised LM objective**: predict next token (or masked tokens) → labels are free from the text itself.
- **Post-training**: SFT (instruction pairs), preference data (RLHF/DPO etc.) — smaller, higher-curation datasets on top of a pretrained base.
- **Compute + batching + precision** (fp16/bf16, grad accum) are part of the "information pipeline" in practice, not just the corpus.

### Unsupervised / RL data shapes (one-liners)

- Unsupervised: raw x only; objective defines the "label" (reconstruction, contrastive pairs, clustering assignment…).
- RL: trajectories (s, a, r, s') or online env interaction; need reward design (or learned reward models) — different failure mode than bad class labels.

---

## Mental model that sticks

**Training = optimize parameters to reduce average loss on examples that stand in for the world you care about.**
Generalization is the actual product; training loss is a proxy. Data quantity, data **coverage**, label quality, split hygiene, and inductive bias of the model family jointly decide whether the proxy is honest.

---

## Python ML stack

Layered mental model (bottom → top):

| Layer | Libraries | Role |
|-------|-----------|------|
| Arrays / numerics | **NumPy**, SciPy | `ndarray`, ufuncs, BLAS/LAPACK linear algebra; foundation everything else speaks |
| Tables / wrangling | **pandas** | Series/DataFrame; CSV/Parquet/SQL/Excel I/O; joins, groupby, time series |
| Classical ML | **scikit-learn** | fit/predict estimators, pipelines, CV, metrics; SVM, forests, k-means, preprocessing |
| Deep learning | **PyTorch** (dominant research/LLM), TensorFlow/Keras | Tensors on CPU/GPU, autograd, `nn.Module`, optimizers, training loops |
| Data for DL at scale | **Hugging Face datasets**, WebDataset, TFRecords… | Load/process/stream huge corpora; Arrow zero-copy; Hub sharing |
| NLP plumbing | **tokenizers**, sentencepiece, HF **transformers** | Text → token ids; pretrained architectures & training heads |
| Viz / experiment | Matplotlib, wandb/tensorboard… | Curves, images, run tracking |

### NumPy (the substrate)
- Multidimensional **homogeneous** arrays (`ndarray`) + math ops; C/Fortran-speed inner loops without leaving Python for every multiply.
- Strided memory views; can wrap external buffers (interop with OpenCV, SciPy sparse needs `scipy.sparse` — dense NumPy alone ≠ sparse MATLAB).
- Releases GIL on many ops → real multithreading in numeric kernels.
- Lineage: Numeric → Numarray → unified **NumPy** (Oliphant ~2006). Still the interchange format classical ML expects.

### pandas
- Built on NumPy. **Series** (1-D labeled) + **DataFrame** (2-D columns as Series).
- Label alignment on ops (join-like); `loc` / `iloc`; missing-data tooling.
- Name from *panel data* + "Python data analysis" (Wes McKinney, AQR, open-sourced ~2009).
- Day-to-day: load messy tables → clean/feature columns → hand arrays to sklearn/torch.

### scikit-learn
- "Batteries included" **classical** ML on CPU NumPy arrays (and growing Array API / GPU-adjacent paths).
- Uniform API: `estimator.fit(X, y)`, `.predict`, **Pipeline**, train_test_split, GridSearchCV.
- Algorithms: SVM (LIBSVM), linear models (LIBLINEAR), ensembles, clustering, decomposition, preprocessing.
- Not the home of billion-param transformers — but still the default for tabular ML, baselines, and feature pipelines.

### PyTorch (training engines)
- **Tensor** ≈ NumPy array + device (CPU/CUDA/ROCm/MPS) + **autograd** tape.
- Forward builds a DAG of ops; `loss.backward()` fills `.grad`; optimizer `step()` updates params.
- `torch.nn.Module`: define `forward()`; layers (Linear, Conv, attention stacks…), losses, dropout, etc.
- Serialize often as `.pt`/`.pth` (zip + pickle weights — beware untrusted pickles).
- Ecosystem gravity: HF Transformers, many LLM stacks, Tesla/Uber examples historically cited; PyTorch 2.x **torch.compile**/TorchDynamo for speedups.
- TensorFlow/Keras: still major (esp. production/Google-shaped stacks); same ideas (graphs/tapes, Keras high-level fit loop).

### Hugging Face `datasets`
- One-liners to load Hub datasets (text/audio/vision/tabular…).
- **Apache Arrow** backend: memory-map / zero-copy columns; process without pulling entire corpora into Python lists.
- Map/filter/shuffle, **streaming** for data larger than RAM, export toward PyTorch/TF/JAX/NumPy/pandas.
- Pairs with **tokenizers** + **transformers** for the modern NLP train path: `load_dataset` → tokenize batched → `DataLoader` → train.

### How a minimal modern train script feels (conceptual)
```text
raw text/table
  → pandas or datasets (clean, filter, split)
  → tokenizer (if text) → input_ids, attention_mask, labels
  → torch DataLoader mini-batches
  → model(**batch) → loss
  → loss.backward(); optim.step(); zero_grad
  → eval on val; checkpoint; repeat epochs
```

### Deep learning one-liners (from DL page)
- **Deep** = many layers / long credit-assignment path; learns a **hierarchy of features** (edges → parts → objects) instead of only hand-engineered inputs.
- Architectures: MLP, CNN, RNN/LSTM, Transformer, GAN, etc.
- Universal approximation: even shallow nets are powerful in theory; depth helps **feature** learning efficiency in practice.
- Unlabeled data abundance → unsupervised/self-supervised pretraining is strategically huge.

### Embeddings & tokens (text path)
- **Word embedding**: map tokens to dense vectors so geometry ≈ meaning (Firth: "company it keeps"); word2vec era → static vectors; modern **contextual** embeddings (ELMo/BERT/GPT-family hidden states) depend on full sentence context.
- Training data for LMs is ultimately **token id sequences**; vocab/tokenizer choice is part of the dataset contract (BPE etc. — deepen later if needed).

---

## Open threads (not finished this pass)

- Concrete transformer block + attention math
- Tokenization algorithms in detail (BPE/Unigram)
- Full LLM post-training stack (SFT → reward model → PPO/DPO)
- JAX/Flax, MLX, and non-Python edges
- Dataset licensing, dedup, contamination, eval harness design
- Hands-on mini train loop in this sandbox (if packages allow)

---

## Sources touched (fundamentals)

- https://en.wikipedia.org/wiki/Machine_learning
- https://en.wikipedia.org/wiki/Gradient_descent
- https://en.wikipedia.org/wiki/Backpropagation
- https://en.wikipedia.org/wiki/Supervised_learning
- https://en.wikipedia.org/wiki/Stochastic_gradient_descent
- https://en.wikipedia.org/wiki/NumPy
- https://en.wikipedia.org/wiki/Pandas_(software)
- https://en.wikipedia.org/wiki/Scikit-learn
- https://en.wikipedia.org/wiki/PyTorch
- https://en.wikipedia.org/wiki/Deep_learning
- https://en.wikipedia.org/wiki/Word_embedding
- https://huggingface.co/docs/datasets/index
