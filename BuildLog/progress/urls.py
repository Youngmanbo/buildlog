from django.urls import path
from . import views

urlpatterns = [
    path("test/", views.test, name="test"),
    path("site_map/", views.site_map, name="site_map"),
    path("picker/", views.picker, name="picker"),
    path("tower/", views.tower, name="tower"),
    
    # 화면
    path("towers/", views.towers_view, name="towers"),

    # API
    path("api/slot/<int:slot_id>/materials/", views.api_slot_materials, name="api_slot_materials"),
    path("api/slot/<int:slot_id>/materials/save/", views.api_slot_materials_save, name="api_slot_materials_save"),
]