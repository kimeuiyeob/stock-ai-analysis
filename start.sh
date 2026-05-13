#!/usr/bin/env bash
# ============================================================
#  financial-ai  —  원클릭 셋업 & 실행 스크립트
#  사용법: bash start.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 색상 ────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
hr()    { echo -e "${BOLD}─────────────────────────────────────────${NC}"; }

hr
echo -e "${BOLD}  financial-ai — 자동 셋업 & 실행${NC}"
hr

# ══════════════════════════════════════════════════════════════
# 1. Python 버전 확인
# ══════════════════════════════════════════════════════════════
info "Python 버전 확인 중..."

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
            PYTHON="$cmd"
            ok "Python $VER 확인됨 ($cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.9 이상이 필요합니다. 설치 후 다시 실행하세요."
    exit 1
fi

# ══════════════════════════════════════════════════════════════
# 2. 가상환경 생성 & 활성화
# ══════════════════════════════════════════════════════════════
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV_DIR/bin/activate" ] && [ ! -f "$VENV_DIR/Scripts/activate" ]; then
    [ -d "$VENV_DIR" ] && rm -rf "$VENV_DIR"
    info "가상환경 생성 중 (.venv)..."
    if ! "$PYTHON" -m venv "$VENV_DIR" 2>/dev/null; then
        warn "python3-venv 패키지가 없습니다. 자동 설치를 시도합니다..."
        PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if command -v apt-get &>/dev/null; then
            sudo apt-get install -y "python${PY_VER}-venv" || sudo apt-get install -y python3-venv
        elif command -v apt &>/dev/null; then
            sudo apt install -y "python${PY_VER}-venv" || sudo apt install -y python3-venv
        else
            error "패키지 매니저를 찾을 수 없습니다. 아래 명령어로 직접 설치하세요:"
            echo -e "  ${CYAN}sudo apt install python${PY_VER}-venv${NC}"
            exit 1
        fi
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    ok "가상환경 생성 완료"
else
    ok "기존 가상환경 사용 (.venv)"
fi

# OS별 활성화
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    ACTIVATE="$VENV_DIR/Scripts/activate"
else
    ACTIVATE="$VENV_DIR/bin/activate"
fi

# shellcheck disable=SC1090
source "$ACTIVATE"
ok "가상환경 활성화됨"

# ══════════════════════════════════════════════════════════════
# 3. 패키지 설치
# ══════════════════════════════════════════════════════════════
info "패키지 설치 중 (requirements.txt)..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
ok "패키지 설치 완료"

# ══════════════════════════════════════════════════════════════
# 4. .gitignore 검사 — .env가 추적되지 않는지 확인
# ══════════════════════════════════════════════════════════════
info ".gitignore 보안 확인 중..."

GITIGNORE_OK=true
for sensitive in ".env" "api_guide/.env"; do
    if grep -qF "$sensitive" "$SCRIPT_DIR/.gitignore" 2>/dev/null; then
        ok ".gitignore에 '$sensitive' 등록됨"
    else
        warn ".gitignore에 '$sensitive' 누락 — 자동 추가합니다"
        echo "$sensitive" >> "$SCRIPT_DIR/.gitignore"
        GITIGNORE_OK=false
    fi
done

if [ "$GITIGNORE_OK" = false ]; then
    warn ".gitignore를 업데이트했습니다. git add .gitignore 로 커밋하세요."
fi

# ══════════════════════════════════════════════════════════════
# 5. .env 파일 확인 및 생성
# ══════════════════════════════════════════════════════════════
ENV_FILE="$SCRIPT_DIR/.env"
ENV_TEMPLATE="$SCRIPT_DIR/.env.example"

# .env.example이 없으면 생성
if [ ! -f "$ENV_TEMPLATE" ]; then
    cat > "$ENV_TEMPLATE" <<'EOF'
# ── 필수 ────────────────────────────────────────────────────
# OpenAI API 키 (LLM 리포트 생성에 사용)
# https://platform.openai.com/api-keys
OPENAI_API_KEY=sk-...

# ── 선택 (없으면 해당 데이터소스 비활성화됨) ─────────────────
# NewsAPI — 최신 뉴스 감성 분석
# https://newsapi.org/register
NEWS_API_KEY=

# FRED (Federal Reserve) — 금리·CPI·GDP 등 거시경제 지표
# https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY=

# Finnhub — EPS 서프라이즈, 내부자 거래
# https://finnhub.io/register
FINNHUB_API_KEY=

# Alpha Vantage — 연간 손익계산서, EPS 추정치
# https://www.alphavantage.co/support/#api-key
ALPHA_VANTAGE_API_KEY=
EOF
    ok ".env.example 생성 완료"
fi

# .env 없으면 안내 후 생성 유도
if [ ! -f "$ENV_FILE" ]; then
    warn ".env 파일이 없습니다."
    echo ""
    echo -e "  ${BOLD}.env.example${NC} 파일을 복사하고 API 키를 입력하세요:"
    echo -e "  ${CYAN}cp .env.example .env${NC}"
    echo -e "  ${CYAN}nano .env${NC}  (또는 원하는 편집기 사용)"
    echo ""
    echo -e "  최소 ${BOLD}OPENAI_API_KEY${NC}는 필수입니다."
    echo ""

    read -r -p "지금 바로 OPENAI_API_KEY를 입력하시겠습니까? [y/N] " REPLY
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        read -r -p "  OPENAI_API_KEY: " OKEY
        if [ -n "$OKEY" ]; then
            cp "$ENV_TEMPLATE" "$ENV_FILE"
            # 키 치환
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s|OPENAI_API_KEY=sk-...|OPENAI_API_KEY=$OKEY|" "$ENV_FILE"
            else
                sed -i "s|OPENAI_API_KEY=sk-...|OPENAI_API_KEY=$OKEY|" "$ENV_FILE"
            fi
            ok ".env 생성 완료 (OPENAI_API_KEY 등록됨)"
        else
            error "키가 비어 있습니다. .env 파일을 직접 작성한 후 다시 실행하세요."
            exit 1
        fi
    else
        error ".env 없이는 실행할 수 없습니다. 위 안내대로 .env를 생성한 후 다시 실행하세요."
        exit 1
    fi
fi

# .env 로드
# shellcheck disable=SC2046
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
ok ".env 로드 완료"

# ══════════════════════════════════════════════════════════════
# 6. API 키 상태 확인
# ══════════════════════════════════════════════════════════════
hr
echo -e "${BOLD}  API 키 상태${NC}"
hr

check_key() {
    local name="$1" val="${!1:-}" required="${2:-optional}"
    if [ -n "$val" ] && [ "$val" != "sk-..." ] && [ "${#val}" -gt 4 ]; then
        ok "$name ✓ (${val:0:6}...)"
    elif [ "$required" = "required" ]; then
        error "$name — 필수 키가 비어 있습니다. .env를 확인하세요."
        exit 1
    else
        warn "$name — 미설정 (해당 데이터소스 비활성화)"
    fi
}

check_key "OPENAI_API_KEY"     "required"
check_key "NEWS_API_KEY"       "optional"
check_key "FRED_API_KEY"       "optional"
check_key "FINNHUB_API_KEY"    "optional"
check_key "ALPHA_VANTAGE_API_KEY" "optional"

# ══════════════════════════════════════════════════════════════
# 7. 필수 디렉토리 생성
# ══════════════════════════════════════════════════════════════
hr
info "디렉토리 구조 확인 중..."

for dir in artifacts reports tracking logs; do
    if [ ! -d "$SCRIPT_DIR/$dir" ]; then
        mkdir -p "$SCRIPT_DIR/$dir"
        ok "$dir/ 생성됨"
    else
        ok "$dir/ 확인됨"
    fi
done

# ══════════════════════════════════════════════════════════════
# 8. 실행 모드 선택
# ══════════════════════════════════════════════════════════════
hr
echo -e "${BOLD}  실행 모드 선택${NC}"
hr
echo "  1) 대시보드 실행  (Streamlit — app.py)"
echo "  2) 파이프라인 실행 (단일 티커 분석)"
echo "  3) 종료"
echo ""
read -r -p "  선택 [1]: " MODE
MODE="${MODE:-1}"

case "$MODE" in
    1)
        hr
        info "Streamlit 대시보드 시작 중..."
        ok "브라우저에서 http://localhost:8501 로 접속하세요"
        hr
        streamlit run app.py --server.port 8501
        ;;
    2)
        echo ""
        read -r -p "  분석할 티커를 입력하세요 (예: AAPL): " TICKER
        TICKER="${TICKER:-AAPL}"
        TICKER="${TICKER^^}"
        hr
        info "파이프라인 실행 중: $TICKER"
        hr
        python scripts/run_pipeline.py --ticker "$TICKER"
        ;;
    3)
        info "종료합니다."
        exit 0
        ;;
    *)
        warn "잘못된 선택입니다. 대시보드를 실행합니다."
        streamlit run app.py --server.port 8501
        ;;
esac
