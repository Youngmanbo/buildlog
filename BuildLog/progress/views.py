from django.shortcuts import render
from decimal import Decimal
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction
from django.forms.models import model_to_dict
import json

from .models import Tower, UnitSlot, UnitType, TypeRateUsage, UsageEntry, ProgressPeriod, RateItem

# Create your views here.

def test(request):
    return render(request, "progress/test.html")

def site_map(request):
    return render(request, "progress/claude.html")

def picker(request):
    return render(request, "progress/picker.html")

def tower(request):
    return render(request, "progress/tower_grid.html")

def towers_view(request):
    """
    tower_grid 렌더
    - period 선택(기본 최근/현재)
    - slot 목록을 tower별로 구성해서 템플릿에 전달
    """
    period_id = request.GET.get("period")
    if period_id:
        period = get_object_or_404(ProgressPeriod, pk=period_id)
    else:
        period = ProgressPeriod.objects.order_by("-id").first()

    towers = Tower.objects.order_by("number").prefetch_related("slots")
    # 템플릿에서 그리기 쉽게 tower->floor->line 구조로 정리
    tower_payload = []
    for t in towers:
        slots = list(t.slots.filter(is_active=True).order_by("-floor", "line_no"))
        floors = sorted({s.floor for s in slots}, reverse=True)  # 15..1..-1
        max_line = max([s.line_no for s in slots], default=0)
        slot_map = {(s.floor, s.line_no): s for s in slots}

        tower_payload.append({
            "tower": t,
            "floors": floors,
            "max_line": max_line,
            "slot_map": slot_map,  # 템플릿에서 dict lookup은 직접 안 되니 아래 템플릿에서는 커스텀 태그/JS 사용 권장
        })

    return render(request, "progress/tower_grid_live.html", {
        "period": period,
        "towers": towers,
    })


@require_GET
def api_slot_materials(request, slot_id):
    """
    slot 클릭 시:
    - slot이 세대면: unit_type 기반으로 TypeRateUsage에서 자재 목록 생성
    - slot이 시설이면: (시설별 표준사용량 테이블을 만들거나) 일단 빈 리스트/수기 입력 형태로 시작 가능
    - period를 받아서 기존 입력(UsageEntry)도 함께 반환
    """
    slot = get_object_or_404(UnitSlot, pk=slot_id, is_active=True)
    period_id = request.GET.get("period")
    period = get_object_or_404(ProgressPeriod, pk=period_id) if period_id else ProgressPeriod.objects.order_by("-id").first()

    items = []

    if slot.kind == UnitSlot.KIND_UNIT and slot.unit_type_id:
        usages = (TypeRateUsage.objects
                  .filter(unit_type_id=slot.unit_type_id)
                  .select_related("rate_item")
                  .order_by("rate_item__code"))
        for u in usages:
            planned = u.qty_per_unit
            entry = UsageEntry.objects.filter(period=period, slot=slot, rate_item=u.rate_item).first()
            items.append({
                "rate_item_id": u.rate_item_id,
                "code": u.rate_item.code,
                "name": u.rate_item.name,
                "spec": u.rate_item.spec,
                "unit": u.rate_item.unit,
                "planned_qty": str(planned),
                "used_qty": str(entry.used_qty) if entry else "0",
                "note": entry.note if entry else "",
            })
    else:
        # 시설/기타: 단계1에서는 빈 목록 + "추가" 기능(수기)로 시작 추천
        items = []

    payload = {
        "slot": {
            "id": slot.id,
            "tower": str(slot.tower),
            "floor": slot.floor,
            "line_no": slot.line_no,
            "kind": slot.kind,
            "unit_type": slot.unit_type_id,
            "label": slot.label,
        },
        "period": {"id": period.id, "name": getattr(period, "name", str(period.id))},
        "items": items,
    }
    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})


@require_POST
@transaction.atomic
def api_slot_materials_save(request, slot_id):
    """
    사용량 저장:
    body: { "period_id": 1, "rows": [ {rate_item_id, used_qty, note}, ... ] }
    planned_qty는 서버에서 다시 계산해서 저장(표준사용량 변경에도 일관성 확보)
    """
    slot = get_object_or_404(UnitSlot, pk=slot_id, is_active=True)
    data = json.loads(request.body.decode("utf-8"))
    period = get_object_or_404(ProgressPeriod, pk=data["period_id"])

    rows = data.get("rows", [])
    saved = 0

    # slot이 세대면 unit_type 기반 planned 계산
    planned_map = {}
    if slot.kind == UnitSlot.KIND_UNIT and slot.unit_type_id:
        for u in TypeRateUsage.objects.filter(unit_type_id=slot.unit_type_id):
            planned_map[u.rate_item_id] = u.qty_per_unit

    for r in rows:
        rate_item_id = r["rate_item_id"]
        used_qty = Decimal(str(r.get("used_qty", "0") or "0"))
        note = r.get("note", "") or ""

        planned_qty = planned_map.get(rate_item_id, Decimal("0"))

        obj, _created = UsageEntry.objects.update_or_create(
            period=period, slot=slot, rate_item_id=rate_item_id,
            defaults={"planned_qty": planned_qty, "used_qty": used_qty, "note": note},
        )
        saved += 1

    return JsonResponse({"ok": True, "saved": saved})
