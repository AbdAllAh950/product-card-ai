#!/usr/bin/env bash
# product-card-ai на локальной модели через Ollama: без ключа API, данные не уходят в облако.
#
#   bash scripts/run_local.sh                    # Qwen2.5 3B: подходит и для 8 ГБ ОЗУ
#   MODEL=qwen2.5:7b bash scripts/run_local.sh   # любая другая модель Ollama
#
# Что делает: ставит Ollama без Homebrew (из GitHub Releases), запускает сервер, скачивает модель
# (сначала с зеркала ModelScope, затем из реестра Ollama), создаёт копию модели с контекстом 16K,
# генерирует карточки для examples/input/*.json в examples/output-local/ и сравнивает их
# с карточками облачной модели из examples/output/.
set -u
cd "$(dirname "$0")/.."

CTX=16384
REL=https://github.com/ollama/ollama/releases/latest/download
export NO_PROXY="localhost,127.0.0.1${NO_PROXY:+,$NO_PROXY}" no_proxy="localhost,127.0.0.1${no_proxy:+,$no_proxy}"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\n[ошибка] %s\n' "$*" >&2; exit 1; }

# --- 1. Ollama ------------------------------------------------------------------------------
find_ollama() {
  command -v ollama 2>/dev/null && return
  local app
  for app in /Applications/Ollama.app "$HOME/Applications/Ollama.app"; do
    [ -d "$app" ] && find "$app" -type f -name ollama -perm -111 2>/dev/null | head -1 && return
  done
}

fetch() {  # прерывается, если скорость ниже 1 КБ/с дольше минуты (так выглядит замедление)
  curl -fL --retry 2 --connect-timeout 20 --speed-limit 1024 --speed-time 60 --progress-bar -o "$2" "$1"
}

install_ollama() {
  local tmp dest mnt
  tmp="$(mktemp -d)"
  dest=/Applications
  [ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }
  if fetch "$REL/Ollama-darwin.zip" "$tmp/ollama.zip"; then
    ditto -xk "$tmp/ollama.zip" "$dest" || return 1
  elif fetch "$REL/Ollama.dmg" "$tmp/ollama.dmg"; then
    mnt="$(hdiutil attach -nobrowse -readonly "$tmp/ollama.dmg" | awk -F'\t' '/\/Volumes\//{print $NF}' | tail -1)"
    [ -n "$mnt" ] || return 1
    ditto "$mnt/Ollama.app" "$dest/Ollama.app"
    hdiutil detach "$mnt" -quiet
  else
    return 1
  fi
  rm -rf "$tmp"
}

OLLAMA="$(find_ollama)"
if [ -z "$OLLAMA" ]; then
  say "Ollama не найдена — ставлю из GitHub Releases, без Homebrew"
  install_ollama || die "Не удалось скачать Ollama. Скачайте приложение вручную с https://ollama.com/download, перенесите в «Программы» и запустите скрипт снова."
  OLLAMA="$(find_ollama)"
  [ -n "$OLLAMA" ] || die "Ollama.app установлена, но бинарник не найден. Откройте Ollama.app один раз и запустите скрипт снова."
fi
say "Ollama: $OLLAMA"

# --- 2. Сервер ------------------------------------------------------------------------------
up() { curl -fsS --max-time 2 http://localhost:11434/api/version >/dev/null 2>&1; }
if ! up; then
  say "Запускаю сервер Ollama"
  nohup "$OLLAMA" serve >"${TMPDIR:-/tmp}/ollama-serve.log" 2>&1 &
  for _ in $(seq 1 30); do up && break; sleep 1; done
  up || die "Сервер Ollama не запустился. Лог: ${TMPDIR:-/tmp}/ollama-serve.log"
fi

# --- 3. Модель ------------------------------------------------------------------------------
have_model() { "$OLLAMA" list 2>/dev/null | awk 'NR>1{print $1}' | grep -qix "$1"; }

pull_watch() {  # ollama pull, который сдаётся, если загрузка стоит 3 минуты
  local name=$1 dir="${OLLAMA_MODELS:-$HOME/.ollama/models}" last=-1 still=0 cur pid
  say "Скачиваю модель $name"
  "$OLLAMA" pull "$name" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 15
    cur=$(du -sk "$dir" 2>/dev/null | cut -f1)
    if [ "$cur" = "$last" ]; then still=$((still + 15)); else still=0; last=$cur; fi
    if [ "$still" -ge 180 ]; then
      printf '\nЗагрузка стоит 3 минуты — пробую другой источник.\n'
      kill "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      return 1
    fi
  done
  wait "$pid"
}

if [ -n "${MODEL:-}" ]; then
  BASE=$MODEL
  have_model "$BASE" || pull_watch "$BASE" || die "Не удалось скачать $BASE."
else
  MIRROR=modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M  # зеркало ModelScope, обычно доступно из РФ
  REGISTRY=qwen2.5:3b                                        # официальный реестр Ollama
  if have_model "$REGISTRY"; then BASE=$REGISTRY
  elif have_model "$MIRROR"; then BASE=$MIRROR
  elif pull_watch "$MIRROR"; then BASE=$MIRROR
  elif pull_watch "$REGISTRY"; then BASE=$REGISTRY
  else die "Модель не скачалась ни с ModelScope, ни из реестра Ollama. Включите VPN и запустите скрипт снова (скачанное не потеряется)."
  fi
fi

# По умолчанию Ollama держит короткий контекст; длинный промпт обрезается с начала и теряется
# системная инструкция. Поэтому делаем копию модели с контекстом 16K.
LOCAL="$(printf '%s' "${BASE##*/}" | tr ':/' '--' | tr '[:upper:]' '[:lower:]')-ctx16k"
say "Готовлю $LOCAL (на базе $BASE, контекст $CTX токенов)"
printf 'FROM %s\nPARAMETER num_ctx %s\n' "$BASE" "$CTX" >"${TMPDIR:-/tmp}/Modelfile.product-card"
"$OLLAMA" create "$LOCAL" -f "${TMPDIR:-/tmp}/Modelfile.product-card" >/dev/null || die "ollama create не сработал."

# --- 4. Генерация ---------------------------------------------------------------------------
PY=.venv/bin/python
[ -x "$PY" ] || die "Нет .venv. Создайте: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

export LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL="$LOCAL" LLM_API_KEY= LLM_PROJECT= \
       LLM_TIMEOUT=600 LLM_MAX_RETRIES=1
OUT=examples/output-local
mkdir -p "$OUT"
: >"$OUT/timings.tsv"
say "Генерирую карточки на $LOCAL (первый запрос дольше: модель загружается в память)"
for f in examples/input/*.json; do
  t0=$(date +%s)
  "$PY" -m app.cli "$f" --out "$OUT" --save-raw
  printf '%s\t%s\n' "$(basename "$f" .json)" "$(($(date +%s) - t0))" >>"$OUT/timings.tsv"
done

say "Сравнение с облачной моделью"
"$PY" scripts/compare.py examples/output "$OUT" | tee "$OUT/COMPARISON.md"
