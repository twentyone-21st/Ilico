import os
import re
import json
import pickle
import logging
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from sklearn.naive_bayes import MultinomialNB
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

load_dotenv()

_OPUS_LOCAL     = Path(__file__).parent / "opus_model"
OPUS_MODEL      = str(_OPUS_LOCAL) if _OPUS_LOCAL.exists() else "Helsinki-NLP/opus-mt-en-es"

MODEL_CACHE     = Path(__file__).parent / "modelo_spam.pkl"
CHECKPOINT_FILE = Path(__file__).parent / "traduccion_checkpoint.json"
BATCH_SIZE      = 32
N_POR_CLASE     = 5_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


STOPWORDS = {
    "de","la","el","en","y","a","los","se","del","que","un","una","es","por",
    "con","no","su","al","para","como","mas","pero","sus","le","ya","o","este",
    "si","porque","esta","entre","cuando","muy","sin","sobre","tambien","me",
    "the","to","of","and","in","is","it","you","that","was","for","on","are",
    "with","as","at","be","this","have","from","or","an","by","not","we","our",
}

def preprocesar(texto: str) -> str:
    texto = str(texto).lower()
    texto = re.sub(r'http\S+|www\S+',  ' url_enlace ',    texto)
    texto = re.sub(r'\$[\d,\.]+',      ' monto_dinero ',  texto)
    texto = re.sub(r'\b\d{4}\b',       ' codigo_cuatro ', texto)
    texto = re.sub(r'\d+',             ' numero ',        texto)
    texto = re.sub(r'[^\w\s]',         ' ',               texto)
    tokens = [t for t in texto.split() if t not in STOPWORDS and len(t) > 2]
    return " ".join(tokens)


def cargar_enron() -> pd.DataFrame:
    logger.info("Descargando SetFit/enron_spam desde Hugging Face...")
    from datasets import load_dataset, disable_progress_bar, logging as ds_log
    ds_log.set_verbosity_error()
    disable_progress_bar()

    ds  = load_dataset("SetFit/enron_spam", split="train")
    df  = ds.to_pandas()

    if "text" not in df.columns:
        sujeto  = df.get("subject",  pd.Series("", index=df.index)).fillna("")
        cuerpo  = df.get("message",  pd.Series("", index=df.index)).fillna("")
        df["text"] = (sujeto + " " + cuerpo).str.strip()

    if "label" in df.columns:
        df = df[df["label"].isin([0, 1])].copy()
        df["etiqueta"] = df["label"].map({0: "ham", 1: "spam"})
    elif "label_text" in df.columns:
        df["etiqueta"] = df["label_text"].str.lower()
        df = df[df["etiqueta"].isin(["ham", "spam"])].copy()
    else:
        raise ValueError("El dataset no tiene columna 'label' ni 'label_text'.")

    df  = df[["text", "etiqueta"]].dropna()
    df["text"] = df["text"].astype(str).str.strip()
    df  = df[df["text"].str.len() > 10].reset_index(drop=True)

    n_ham  = (df.etiqueta == "ham").sum()
    n_spam = (df.etiqueta == "spam").sum()
    logger.info(f"  Dataset cargado: {len(df):,} correos — {n_ham:,} ham / {n_spam:,} spam")
    return df


def extraer_muestra(df: pd.DataFrame, n_por_clase: int) -> pd.DataFrame:
    n_h = min(n_por_clase, (df.etiqueta == "ham").sum())
    n_s = min(n_por_clase, (df.etiqueta == "spam").sum())
    ham  = df[df.etiqueta == "ham"].sample(n=n_h,  random_state=42)
    spam = df[df.etiqueta == "spam"].sample(n=n_s, random_state=42)
    return pd.concat([ham, spam]).sample(frac=1, random_state=42)


def traducir_con_nllb(df: pd.DataFrame) -> pd.DataFrame:
    from transformers import pipeline as hf_pipeline

    df = df.reset_index(drop=True)

    checkpoint: dict = {}
    if CHECKPOINT_FILE.exists():
        try:
            checkpoint = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            logger.info(f"  Checkpoint encontrado: {len(checkpoint):,} textos ya traducidos.")
        except Exception:
            checkpoint = {}

    pendientes_total = sum(1 for i in range(len(df)) if str(i) not in checkpoint)
    if pendientes_total == 0:
        logger.info("  Todas las traducciones ya están en el checkpoint.")
        textos = [checkpoint[str(i)] for i in range(len(df))]
        return pd.DataFrame({"text": textos, "etiqueta": df["etiqueta"].tolist()})

    logger.info(f"  Cargando Opus-MT desde {OPUS_MODEL}...")
    traductor = hf_pipeline("translation_en_to_es", model=OPUS_MODEL, device=-1)
    logger.info(f"  Modelo cargado. Traduciendo {pendientes_total:,} textos pendientes...")

    total            = len(df)
    lotes_procesados = 0

    for start in range(0, total, BATCH_SIZE):
        fin        = min(start + BATCH_SIZE, total)
        pendientes = [i for i in range(start, fin) if str(i) not in checkpoint]
        if not pendientes:
            continue

        textos_batch = [df.at[i, "text"][:500] for i in pendientes]

        try:
            resultados = traductor(textos_batch, max_length=512)
            for idx, res in zip(pendientes, resultados):
                checkpoint[str(idx)] = res["translation_text"]

            CHECKPOINT_FILE.write_text(
                json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8"
            )
            lotes_procesados += 1
            if lotes_procesados % 10 == 0:
                logger.info(f"  Progreso: {len(checkpoint):,}/{total:,} textos traducidos...")

        except Exception as e:
            logger.warning(f"  Error en batch {start}–{fin}: {e}. Saltando...")

    textos_finales    = [checkpoint.get(str(i), df.at[i, "text"]) for i in range(total)]
    etiquetas_finales = df["etiqueta"].tolist()

    n_traducidos = sum(1 for i in range(total) if str(i) in checkpoint)
    logger.info(f"  Traducción finalizada: {n_traducidos:,}/{total:,} textos al español.")
    return pd.DataFrame({"text": textos_finales, "etiqueta": etiquetas_finales})


def construir_pipeline() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(
            sublinear_tf=True,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.95,
            strip_accents="unicode",
            analyzer="word",
        )),
        ("nb", MultinomialNB(alpha=0.1)),
    ])


def main(solo_entrenar: bool = False):
    SEP = "═" * 56
    logger.info(SEP)
    logger.info("  ILICO — Reentrenamiento Bilingüe EN + ES")
    logger.info(SEP)

    df_full = cargar_enron()

    logger.info(f"\n[1/4] Muestra A — inglés ({N_POR_CLASE * 2:,} correos)...")
    muestra_a = extraer_muestra(df_full, N_POR_CLASE)
    logger.info(f"  → {len(muestra_a):,} correos seleccionados")

    logger.info(f"\n[2/4] Muestra B — para traducir ({N_POR_CLASE * 2:,} correos)...")
    df_restante = df_full.drop(index=muestra_a.index)
    muestra_b   = extraer_muestra(df_restante, N_POR_CLASE)
    logger.info(f"  → {len(muestra_b):,} correos seleccionados (sin solapamiento con Muestra A)")

    if solo_entrenar:
        logger.info("\n[3/4] Modo solo-entrenar: aplicando checkpoint existente sin traducir más...")
        muestra_b_r = muestra_b.reset_index(drop=True)
        checkpoint: dict = {}
        if CHECKPOINT_FILE.exists():
            checkpoint = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        textos_b = [checkpoint.get(str(i), muestra_b_r.at[i, "text"]) for i in range(len(muestra_b_r))]
        muestra_b_es = pd.DataFrame({"text": textos_b, "etiqueta": muestra_b_r["etiqueta"].tolist()})
        n_es = sum(1 for i in range(len(muestra_b_r)) if str(i) in checkpoint)
        logger.info(f"  {n_es:,} correos en español, {len(muestra_b_r)-n_es:,} en inglés (fallback)")
    else:
        logger.info("\n[3/4] Traduciendo Muestra B al español con Opus-MT...")
        muestra_b_es = traducir_con_nllb(muestra_b)

    logger.info("\n[4/4] Combinando datasets y entrenando el modelo...")
    df_bilingue = (
        pd.concat(
            [muestra_a.reset_index(drop=True), muestra_b_es],
            ignore_index=True,
        )
        .sample(frac=1, random_state=42)
        .reset_index(drop=True)
    )

    n_ham  = (df_bilingue.etiqueta == "ham").sum()
    n_spam = (df_bilingue.etiqueta == "spam").sum()
    logger.info(
        f"  Dataset bilingüe: {len(df_bilingue):,} correos — "
        f"{n_ham:,} ham / {n_spam:,} spam"
    )

    textos    = [preprocesar(t) for t in df_bilingue["text"]]
    etiquetas = df_bilingue["etiqueta"].tolist()

    X_tr, X_te, y_tr, y_te = train_test_split(
        textos, etiquetas,
        test_size=0.20,
        random_state=42,
        stratify=etiquetas,
    )

    modelo   = construir_pipeline()
    modelo.fit(X_tr, y_tr)
    accuracy = accuracy_score(y_te, modelo.predict(X_te))
    logger.info(f"  Precisión en test (20 %): {accuracy * 100:.1f} %")

    with open(MODEL_CACHE, "wb") as f:
        pickle.dump({"modelo": modelo, "accuracy": accuracy}, f)
    logger.info(f"  ✓ Modelo guardado → {MODEL_CACHE}")

    if CHECKPOINT_FILE.exists():
        logger.info(
            f"\n  El checkpoint de traducción está en: {CHECKPOINT_FILE}\n"
            "  Puedes eliminarlo cuando confirmes que el modelo funciona bien.\n"
            "  Si lo conservas, la próxima ejecución reutilizará las traducciones."
        )

    logger.info(f"\n{SEP}")
    logger.info("  Reentrenamiento completado con éxito.")
    logger.info(SEP)


if __name__ == "__main__":
    import sys
    solo_entrenar = "--solo-entrenar" in sys.argv
    main(solo_entrenar=solo_entrenar)
