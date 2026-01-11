from django.core.management.base import BaseCommand
from django.db import transaction
from difflib import SequenceMatcher
import re

from progress.models import RateItem, TakeoffLine, RateCodeMap


def norm_code(x: str) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    s = s.replace("\u00A0", "")
    s = re.sub(r"\s+", "", s)          # 모든 공백 제거
    s = s.replace("-", "").replace("_", "")
    return s.upper()


def norm_text(x: str) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    s = s.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def norm_key(name: str, spec: str, unit: str) -> str:
    # 이름/규격은 공백/기호를 최대한 정리해서 키로 사용
    def clean(s):
        s = norm_text(s)
        s = re.sub(r"[^\w가-힣]+", "", s)   # 한글/영문/숫자 외 제거
        return s.lower()
    return f"{clean(name)}|{clean(spec)}|{norm_text(unit).lower()}"


def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class Command(BaseCommand):
    help = "Link TakeoffLine -> RateItem using (1) normalized code (2) normalized name/spec/unit exact match (3) fuzzy suggestions into RateCodeMap."

    def add_arguments(self, parser):
        parser.add_argument("--threshold", type=float, default=0.88, help="fuzzy 추천 임계치(0~1), 기본 0.88")
        parser.add_argument("--limit", type=int, default=5000, help="fuzzy 추천 최대 건수(기본 5000)")
        parser.add_argument("--dry-run", action="store_true", help="DB 업데이트 없이 결과만 출력")

    @transaction.atomic
    def handle(self, *args, **opts):
        threshold = opts["threshold"]
        limit = opts["limit"]
        dry = opts["dry_run"]

        # --- 인덱스 구축: RateItem by normalized code ---
        rate_items = list(RateItem.objects.all().only("id", "code", "name", "spec", "unit"))
        rate_by_code = {norm_code(r.code): r for r in rate_items if norm_code(r.code)}
        rate_by_key = {}
        rate_by_unit = {}

        for r in rate_items:
            k = norm_key(r.name, r.spec, r.unit)
            if k and k not in rate_by_key:
                rate_by_key[k] = r
            u = norm_text(r.unit).lower()
            rate_by_unit.setdefault(u, []).append(r)

        qs = TakeoffLine.objects.all().only("id", "code", "name", "spec", "unit", "rate_item")
        total = qs.count()

        # 1) 코드 정규화 후 매칭
        code_matched = 0
        for t in qs.filter(rate_item__isnull=True):
            rc = rate_by_code.get(norm_code(t.code))
            if rc:
                code_matched += 1
                if not dry:
                    TakeoffLine.objects.filter(id=t.id).update(rate_item=rc)

        self.stdout.write(self.style.SUCCESS(f"[A] normalized code matched: {code_matched}"))

        # 2) (이름/규격/단위) 정규화 정확일치 매칭
        key_matched = 0
        for t in qs.filter(rate_item__isnull=True):
            k = norm_key(t.name, t.spec, t.unit)
            rc = rate_by_key.get(k)
            if rc:
                key_matched += 1
                if not dry:
                    TakeoffLine.objects.filter(id=t.id).update(rate_item=rc)

        self.stdout.write(self.style.SUCCESS(f"[B] name/spec/unit exact matched: {key_matched}"))

        # 3) fuzzy 추천 저장(사람 검토용): 같은 unit 그룹 내에서 name+spec 유사도
        suggestions = 0
        created = 0
        for t in qs.filter(rate_item__isnull=True):
            if suggestions >= limit:
                break

            unit = norm_text(t.unit).lower()
            candidates = rate_by_unit.get(unit, rate_items)  # unit 없으면 전체
            target = (norm_text(t.name) + " " + norm_text(t.spec)).strip()
            target_clean = re.sub(r"\s+", "", target.lower())

            best = None
            best_score = 0.0

            # 후보가 너무 많으면 name 첫 글자/부분 필터링으로 간단히 줄임
            for r in candidates:
                cand = (norm_text(r.name) + " " + norm_text(r.spec)).strip()
                cand_clean = re.sub(r"\s+", "", cand.lower())
                s = sim(target_clean, cand_clean)
                if s > best_score:
                    best_score = s
                    best = r

            if best and best_score >= threshold:
                suggestions += 1
                # takeoff_code -> rate_code 추천 저장(중복이면 업데이트)
                if not dry:
                    obj, is_new = RateCodeMap.objects.update_or_create(
                        takeoff_code=norm_code(t.code),
                        defaults=dict(rate_code=best.code, confidence=round(best_score * 100, 2)),
                    )
                    if is_new:
                        created += 1

        self.stdout.write(self.style.SUCCESS(
            f"[C] fuzzy suggestions saved: {suggestions} (new rows: {created})  threshold={threshold}"
        ))

        # 4) RateCodeMap 기반 적용 (추천을 실제 링크로 반영)
        map_applied = 0
        if not dry:
            maps = {m.takeoff_code: m.rate_code for m in RateCodeMap.objects.all()}
            for t in qs.filter(rate_item__isnull=True):
                rc = maps.get(norm_code(t.code))
                if rc:
                    r_obj = RateItem.objects.filter(code=rc).first()
                    if r_obj:
                        map_applied += 1
                        TakeoffLine.objects.filter(id=t.id).update(rate_item=r_obj)

        self.stdout.write(self.style.SUCCESS(f"[D] applied RateCodeMap links: {map_applied}"))

        # 최종 결과
        linked = TakeoffLine.objects.filter(rate_item__isnull=False).count()
        self.stdout.write(self.style.SUCCESS(f"FINAL linked: {linked}/{total} ({linked/total*100:.2f}%)"))
