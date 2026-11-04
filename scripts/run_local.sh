#!/usr/bin/env bash
# product-card-ai на локальной модели через Ollama: без ключа API, данные не уходят в облако.
#
#   bash scripts/run_local.sh                    # Qwen2.5 3B: подходит и для 8 ГБ ОЗУ
#   MODEL=qwen2.5:7b bash scripts/run_local.sh   # любая другая модель Ollama
#
# Что делает: ставит Ollama без Homebrew (из GitHub Releases), запускает сервер, скачивает GGUF-файл
# модели обычным HTTPS (ModelScope, затем Hugging Face, затем реестр Ollama — что доступно из вашей
# сети), регистрирует её в Ollama с контекстом 16K, генерирует карточки для examples/input/*.json
# в examples/output-local/ и сравнивает их с карточками облачной модели из examples/output/.
# Весь вывод дублируется в run_local.log.
set -u
cd "$(dirname "$0")/.."
exec > >(tee -a run_local.log) 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="

CTX=16384
GGUF_NAME=qwen2.5-3b-instruct-q4_k_m.gguf
GGUF_URLS="https://modelscope.cn/models/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/master/$GGUF_NAME
https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/$GGUF_NAME"
GGUF_DIR="$HOME/.cache/product-card-ai"
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

gguf_ok() {  # файл целиком: сигнатура GGUF и размер больше 1,8 ГБ
  [ -f "$1" ] && [ "$(head -c 4 "$1")" = "GGUF" ] && [ "$(stat -f %z "$1")" -gt 1800000000 ]
}

download_gguf() {  # докачивает файл с первого доступного источника; прерывает зависшие загрузки
  local url file="$GGUF_DIR/$GGUF_NAME"
  mkdir -p "$GGUF_DIR"
  gguf_ok "$file" && return 0
  for url in $GGUF_URLS; do
    say "Скачиваю модель (~2 ГБ): $url"
    curl -fL -C - --retry 2 --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
         --progress-bar -o "$file" "$url" && gguf_ok "$file" && return 0
    [ "$(head -c 4 "$file" 2>/dev/null)" = "GGUF" ] || rm -f "$file"  # не файл модели — не докачиваем к нему
    printf 'Источник недоступен или загрузка зависла — пробую следующий.\n'
  done
  return 1
}

create_from_gguf() {  # $1 — имя модели в Ollama, $2 — путь к .gguf; шаблон чата Qwen (ChatML)
  local mf="${TMPDIR:-/tmp}/Modelfile.product-card"
  printf 'FROM %s\nPARAMETER num_ctx %s\nPARAMETER stop "<|im_end|>"\n' "$2" "$CTX" >"$mf"
  cat >>"$mf" <<'EOF'
TEMPLATE """{{- range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
"""
EOF
  "$OLLAMA" create "$1" -f "$mf"
}

create_ctx_copy() {  # $1 — новое имя, $2 — модель из реестра Ollama
  local mf="${TMPDIR:-/tmp}/Modelfile.product-card"
  printf 'FROM %s\nPARAMETER num_ctx %s\n' "$2" "$CTX" >"$mf"
  "$OLLAMA" create "$1" -f "$mf"
}

# По умолчанию Ollama держит короткий контекст: длинный промпт обрезается с начала и теряется
# системная инструкция. Поэтому модель регистрируется с контекстом 16K.
if [ -n "${MODEL:-}" ]; then
  LOCAL="$(printf '%s' "${MODEL##*/}" | tr ':/' '--' | tr '[:upper:]' '[:lower:]')-ctx16k"
  have_model "$MODEL" || pull_watch "$MODEL" || die "Не удалось скачать $MODEL."
  create_ctx_copy "$LOCAL" "$MODEL" >/dev/null || die "ollama create не сработал."
else
  LOCAL=qwen2.5-3b-ctx16k
  if have_model "$LOCAL"; then
    say "Модель $LOCAL уже есть"
  elif download_gguf; then
    say "Регистрирую модель в Ollama как $LOCAL"
    create_from_gguf "$LOCAL" "$GGUF_DIR/$GGUF_NAME" || die "ollama create не сработал."
    rm -f "$GGUF_DIR/$GGUF_NAME"  # Ollama хранит свою копию, второй файл на 2 ГБ не нужен
  elif pull_watch qwen2.5:3b; then
    create_ctx_copy "$LOCAL" qwen2.5:3b >/dev/null || die "ollama create не сработал."
  else
    die "Модель не скачалась ни с ModelScope, ни с Hugging Face, ни из реестра Ollama. Включите VPN и запустите скрипт снова: скачанная часть файла не потеряется."
  fi
fi

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
