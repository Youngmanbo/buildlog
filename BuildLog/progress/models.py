from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal

class RateItem(models.Model):
    """일위대가목록(단가)"""
    code = models.CharField(max_length=30, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    spec = models.CharField(max_length=300, blank=True, default="")
    unit = models.CharField(max_length=20, blank=True, default="")

    material_cost = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    labor_cost = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    expense_cost = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    total_cost = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))

    note = models.CharField(max_length=200, blank=True, default="")  # 예: 호표
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} {self.name}"


class TakeoffLine(models.Model):
    """집계(수량산출)"""
    row_key = models.CharField(max_length=64, unique=True, db_index=True)  # 안정적인 고유키(해시)
    line_tag = models.CharField(max_length=120, db_index=True)  # 세대라인
    work_type = models.CharField(max_length=80, db_index=True)  # 공사종류

    code = models.CharField(max_length=30, db_index=True)
    name = models.CharField(max_length=200)
    spec = models.CharField(max_length=300, blank=True, default="")
    unit = models.CharField(max_length=20, blank=True, default="")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))

    rate_item = models.ForeignKey(RateItem, null=True, blank=True, on_delete=models.SET_NULL)

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def contract_amount(self):
        if not self.rate_item:
            return None
        return self.quantity * self.rate_item.total_cost

    def __str__(self):
        return f"{self.line_tag} {self.code} {self.name}"


class ProgressPeriod(models.Model):
    """기성 차수/기간"""
    name = models.CharField(max_length=50, unique=True)  # 예: "1차", "2026-01"
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name


class ProgressEntry(models.Model):
    """기간별 기성(실적)"""
    period = models.ForeignKey(ProgressPeriod, on_delete=models.CASCADE)
    takeoff_line = models.ForeignKey(TakeoffLine, on_delete=models.CASCADE)

    quantity_done = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("period", "takeoff_line")  # 같은 차수에 같은 산출라인 중복 방지

    @property
    def amount(self):
        if not self.takeoff_line.rate_item:
            return None
        return self.quantity_done * self.takeoff_line.rate_item.total_cost

class RateCodeMap(models.Model):
    takeoff_code = models.CharField(max_length=64, unique=True, db_index=True)
    rate_code = models.CharField(max_length=64, db_index=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.takeoff_code} -> {self.rate_code} ({self.confidence})"

class UnitType(models.Model):
    """
    31A, 41C 같은 타입
    """
    code = models.CharField(max_length=10, primary_key=True)
    total_units = models.PositiveIntegerField(default=0)  # 전체 세대수(표의 값)
    name = models.CharField(max_length=50, blank=True, default="")  # 옵션

    def __str__(self):
        return self.code


class Tower(models.Model):
    """
    301동~308동
    """
    number = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=20, blank=True, default="")

    def __str__(self):
        return self.name or f"{self.number}동"


class UnitSlot(models.Model):
    """
    tower_grid의 각 칸(층/호/구역).
    - KIND_UNIT: 세대(타입 존재)
    - KIND_BLOCKED: X 처리(없음)
    - KIND_FACILITY: 초록 영역(관리사무소/어린이집 등)
    - KIND_BASEMENT: 지하1 회색 라인(표시용)
    """
    KIND_UNIT = "UNIT"
    KIND_BLOCKED = "BLOCKED"
    KIND_FACILITY = "FACILITY"
    KIND_BASEMENT = "BASEMENT"

    KIND_CHOICES = [
        (KIND_UNIT, "세대"),
        (KIND_BLOCKED, "제외(X)"),
        (KIND_FACILITY, "부대시설"),
        (KIND_BASEMENT, "지하"),
    ]

    tower = models.ForeignKey(Tower, on_delete=models.CASCADE, related_name="slots")
    floor = models.IntegerField()  # 예: 15, 14 ... 1, 지하는 -1로 통일 추천
    line_no = models.PositiveIntegerField()  # 1호~7호 등

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_UNIT)
    unit_type = models.ForeignKey(UnitType, null=True, blank=True, on_delete=models.SET_NULL)

    label = models.CharField(max_length=50, blank=True, default="")  # 시설명(관리사무소 등)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tower", "floor", "line_no")]

    def __str__(self):
        f = "지하1" if self.floor == -1 else f"{self.floor}F"
        return f"{self.tower}-{f}-{self.line_no}호"


class TypeRateUsage(models.Model):
    """
    타입별 표준 사용량(세대 1개당 자재 소요)
    예: 31A 타입은 'PB관 D16'이 12.3m
    """
    unit_type = models.ForeignKey(UnitType, on_delete=models.CASCADE)
    rate_item = models.ForeignKey("RateItem", on_delete=models.CASCADE)

    qty_per_unit = models.DecimalField(
        max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal("0"))],
        default=Decimal("0")
    )

    class Meta:
        unique_together = [("unit_type", "rate_item")]

    def __str__(self):
        return f"{self.unit_type_id} - {self.rate_item_id}: {self.qty_per_unit}"


class UsageEntry(models.Model):
    """
    특정 기간에 특정 UnitSlot이 실제 사용한 물량 기록
    """
    period = models.ForeignKey("ProgressPeriod", on_delete=models.CASCADE)
    slot = models.ForeignKey(UnitSlot, on_delete=models.CASCADE)
    rate_item = models.ForeignKey("RateItem", on_delete=models.CASCADE)

    planned_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    used_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    note = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("period", "slot", "rate_item")]