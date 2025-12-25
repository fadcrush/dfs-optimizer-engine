"""
URL Configuration for dfs_site project
"""

from django.contrib import admin
from django.urls import path, include
from django.shortcuts import render

def home(request):
    """Home page view"""
    return render(request, 'home.html')

urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
    path('nba/', include('apps.nba.urls')),
    path('nfl/', include('apps.nfl.urls')),
    path('api/', include('apps.api.urls')),
]