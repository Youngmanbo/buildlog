from django.db import models
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
