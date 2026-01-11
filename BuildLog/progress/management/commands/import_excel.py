from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
import pandas as pd
import re
import hashlib

from progress.models import RateItem, TakeoffLine  # 본인 모델명/앱명 맞춰서 수정


# ----------------------------
# Utilities
# ----------------------------
def d(x) -> Decimal:
    """Decimal safe parser (handles commas, blanks, NaN)."""
    if x is None:
        return Decimal("0")
    if isinstance(x, float) and pd.isna(x):
        return Decimal("0")
    s = str(x).strip().replace(",", "")
    if s == "" or s.lower() == "nan":
        return Decimal("0")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")


def norm_text(x) -> str:
    """Normalize general text (strip, remove NBSP, keep internal spaces)."""
    if x is None:
        return ""
    s = str(x).replace("\u00A0", " ").strip()
    return s


def norm_col(x) -> str:
    """
    Normalize column names:
    - remove NBSP
    - remove ALL whitespace (fix '코 드' -> '코드')
    """
    if x is None:
        return ""
    s = str(x).replace("\u00A0", " ")
    s = re.sub(r"\s+", "", s)
    return s.strip()


def pick_col(cols, candidates):
    """Pick first matching column name from candidates."""
    for c in candidates:
        if c in cols:
            return c
    return None


def sha1_key(raw: str) -> str:
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ----------------------------
# Command
# ----------------------------
class Command(BaseCommand):
    help = "Import Excel sheets into DB: RateItem(일위대가목록) + TakeoffLine(집계) and link by code."

    def add_arguments(self, parser):
        # takeoff (집계)
        parser.add_argument("--takeoff", required=True, help="집계.xlsx 경로")
        parser.add_argument("--takeoff-sheet", default="집계", help="집계 시트명 (기본: 집계)")
        parser.add_argument("--takeoff-header", default="0", help="집계 시트 헤더 행(0부터). 기본 0")

        # rate (일위대가목록)
        parser.add_argument("--rate", required=True, help="내역서.xlsx 경로(일위대가목록 포함)")
        parser.add_argument("--rate-sheet", default="일위대가목록", help="일위대가목록 시트명 (기본: 일위대가목록)")
        parser.add_argument("--rate-header", default="2", help="일위대가목록 헤더 행(0부터). 기본 2")

        # debug
        parser.add_argument("--debug", action="store_true", help="컬럼/상위행 출력")

    @transaction.atomic
    def handle(self, *args, **opts):
        takeoff_path = opts["takeoff"]
        takeoff_sheet = opts["takeoff_sheet"]
        takeoff_header = int(opts["takeoff_header"])

        rate_path = opts["rate"]
        rate_sheet = opts["rate_sheet"]
        rate_header = int(opts["rate_header"])

        debug = opts["debug"]

        # ---------------------------------------
        # 1) Load RateItem from 일위대가목록
        # ---------------------------------------
        self.stdout.write(self.style.NOTICE(f"[1] Load rate sheet: {rate_path} / {rate_sheet} (header={rate_header})"))
        df_rate = pd.read_excel(rate_path, sheet_name=rate_sheet, header=rate_header)
        df_rate.columns = [norm_col(c) for c in df_rate.columns]
        cols_rate = df_rate.columns.tolist()

        if debug:
            self.stdout.write(self.style.WARNING(f"RATE COLUMNS: {cols_rate}"))
            self.stdout.write(self.style.WARNING(df_rate.head(5).to_string()))

        # Column mapping for your file (공백 제거 후 기준)
        COL_CODE = pick_col(cols_rate, ["코드", "품목코드", "자재코드", "실적단가코드", "자체실적코드"])
        COL_NAME = pick_col(cols_rate, ["명칭", "품명"])
        COL_SPEC = pick_col(cols_rate, ["형규격", "규격", "규격/표준"])
        COL_UNIT = pick_col(cols_rate, ["단위"])
        COL_MAT  = pick_col(cols_rate, ["재료비"])
        COL_LAB  = pick_col(cols_rate, ["노무비"])
        COL_EXP  = pick_col(cols_rate, ["경비"])
        COL_TOT  = pick_col(cols_rate, ["합계", "총계", "단가", "총단가"])
        COL_NOTE = pick_col(cols_rate, ["비고", "호표", "내역적용비목구분"])

        missing = [("COL_CODE", COL_CODE), ("COL_NAME", COL_NAME), ("COL_UNIT", COL_UNIT), ("COL_TOT", COL_TOT)]
        missing = [k for k, v in missing if v is None]
        if missing:
            raise ValueError(
                f"일위대가목록 필수 컬럼을 못 찾았습니다: {missing}\n"
                f"현재 컬럼={cols_rate}\n"
                f"rate_header={rate_header}가 맞는지, 시트가 맞는지 확인하세요."
            )

        # Drop rows without code
        df_rate = df_rate.dropna(subset=[COL_CODE])

        upserted = 0
        for _, r in df_rate.iterrows():
            code = norm_text(r.get(COL_CODE))
            if not code or code.lower() == "nan":
                continue

            defaults = dict(
                name=norm_text(r.get(COL_NAME)),
                spec=norm_text(r.get(COL_SPEC)) if COL_SPEC else "",
                unit=norm_text(r.get(COL_UNIT)),
                material_cost=d(r.get(COL_MAT)) if COL_MAT else Decimal("0"),
                labor_cost=d(r.get(COL_LAB)) if COL_LAB else Decimal("0"),
                expense_cost=d(r.get(COL_EXP)) if COL_EXP else Decimal("0"),
                total_cost=d(r.get(COL_TOT)),
                note=norm_text(r.get(COL_NOTE)) if COL_NOTE else "",
            )

            RateItem.objects.update_or_create(code=code, defaults=defaults)
            upserted += 1

        self.stdout.write(self.style.SUCCESS(f"  - RateItem upserted: {upserted}"))

        # ---------------------------------------
        # 2) Load TakeoffLine from 집계
        # ---------------------------------------
        self.stdout.write(self.style.NOTICE(f"[2] Load takeoff sheet: {takeoff_path} / {takeoff_sheet} (header={takeoff_header})"))
        df_t = pd.read_excel(takeoff_path, sheet_name=takeoff_sheet, header=takeoff_header)
        df_t.columns = [norm_col(c) for c in df_t.columns]
        cols_t = df_t.columns.tolist()

        if debug:
            self.stdout.write(self.style.WARNING(f"TAKEOFF COLUMNS: {cols_t}"))
            self.stdout.write(self.style.WARNING(df_t.head(5).to_string()))

        # Takeoff column mapping (공백 제거 후 기준)
        COL_LINE = pick_col(cols_t, ["세대라인", "라인", "라인명", "세대"])
        COL_WORK = pick_col(cols_t, ["공사종류", "공종", "공사구분"])
        COL_TCODE = pick_col(cols_t, ["코드", "품목코드", "자재코드"])
        COL_TNAME = pick_col(cols_t, ["품명", "명칭"])
        COL_TSPEC = pick_col(cols_t, ["규격", "형규격"])
        COL_TUNIT = pick_col(cols_t, ["단위"])
        COL_QTY = pick_col(cols_t, ["수량", "할증수량", "합계수량"])

        missing_t = [("COL_LINE", COL_LINE), ("COL_WORK", COL_WORK), ("COL_TCODE", COL_TCODE), ("COL_QTY", COL_QTY)]
        missing_t = [k for k, v in missing_t if v is None]
        if missing_t:
            raise ValueError(
                f"집계 시트 필수 컬럼을 못 찾았습니다: {missing_t}\n"
                f"현재 컬럼={cols_t}\n"
                f"takeoff_header={takeoff_header}가 맞는지, 시트가 맞는지 확인하세요."
            )

        df_t = df_t.dropna(subset=[COL_TCODE])

        takeoff_upserted = 0
        linked = 0

        for _, r in df_t.iterrows():
            line_tag = norm_text(r.get(COL_LINE))
            work_type = norm_text(r.get(COL_WORK))
            code = norm_text(r.get(COL_TCODE))
            if not (line_tag and work_type and code):
                continue

            spec = norm_text(r.get(COL_TSPEC)) if COL_TSPEC else ""
            unit = norm_text(r.get(COL_TUNIT)) if COL_TUNIT else ""
            name = norm_text(r.get(COL_TNAME)) if COL_TNAME else ""

            raw = f"{line_tag}|{work_type}|{code}|{spec}|{unit}|{name}"
            row_key = sha1_key(raw)  # fixed length (40)

            rate = RateItem.objects.filter(code=code).first()

            TakeoffLine.objects.update_or_create(
                row_key=row_key,
                defaults=dict(
                    line_tag=line_tag,
                    work_type=work_type,
                    code=code,
                    name=name,
                    spec=spec,
                    unit=unit,
                    quantity=d(r.get(COL_QTY)),
                    rate_item=rate,
                )
            )

            takeoff_upserted += 1
            if rate:
                linked += 1

        self.stdout.write(self.style.SUCCESS(f"  - TakeoffLine upserted: {takeoff_upserted}"))
        self.stdout.write(self.style.SUCCESS(f"  - Linked to RateItem by code: {linked}/{takeoff_upserted}"))
        self.stdout.write(self.style.SUCCESS("DONE"))
