#!/usr/bin/env bash
# 레거시 데이터 정리 스크립트
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
hr()    { echo -e "${BOLD}─────────────────────────────────────────${NC}"; }

hr
echo -e "${BOLD}  financial-ai — 데이터 정리${NC}"
hr

# 삭제 대상 목록
TARGETS=(
    "artifacts"
    "reports"
)

# 삭제 전 현황 출력
for dir in "${TARGETS[@]}"; do
    if [ -d "$SCRIPT_DIR/$dir" ]; then
        count=$(find "$SCRIPT_DIR/$dir" -mindepth 1 -maxdepth 2 -type d 2>/dev/null | wc -l)
        size=$(du -sh "$SCRIPT_DIR/$dir" 2>/dev/null | cut -f1)
        info "$dir/  →  하위 디렉토리 ${count}개, 용량 ${size}"
    fi
done

# prediction_log.csv 여부 확인
CSV="$SCRIPT_DIR/tracking/prediction_log.csv"
if [ -f "$CSV" ]; then
    lines=$(( $(wc -l < "$CSV") - 1 ))
    warn "tracking/prediction_log.csv  →  예측 이력 ${lines}건 포함"
fi

echo ""
echo -e "  삭제 항목:"
echo -e "  ${RED}[1]${NC} artifacts/ + reports/ 만 삭제"
echo -e "  ${RED}[2]${NC} artifacts/ + reports/ + prediction_log.csv 전부 삭제"
echo -e "  ${RED}[3]${NC} 취소"
echo ""
read -r -p "  선택 [1]: " CHOICE
CHOICE="${CHOICE:-1}"

delete_dirs() {
    for dir in "${TARGETS[@]}"; do
        if [ -d "$SCRIPT_DIR/$dir" ]; then
            find "$SCRIPT_DIR/$dir" -mindepth 1 -delete
            ok "$dir/ 하위 데이터 삭제 완료"
        else
            info "$dir/ 없음 — 건너뜀"
        fi
    done
}

case "$CHOICE" in
    1)
        delete_dirs
        ;;
    2)
        delete_dirs
        if [ -f "$CSV" ]; then
            rm -f "$CSV"
            ok "tracking/prediction_log.csv 삭제 완료"
        fi
        ;;
    3)
        info "취소됐습니다."
        exit 0
        ;;
    *)
        warn "잘못된 선택입니다. 취소됩니다."
        exit 1
        ;;
esac

hr
ok "정리 완료"
hr
