"""
URL configuration for DienstplanerAPI project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from dienstplan.views import WishView, DienstplanView, DienstTradeView, dienstplanservice
from users.views import UserViewSet, check_token, UserRegister, TokenView, InstitutionViewSet, DienstModellViewSet, \
    ChangePasswordView, forgot_password, set_new_password, AbrechnungsViewSet

router = DefaultRouter()

router.register(r'users', UserViewSet)
router.register(r'institution',InstitutionViewSet)
router.register(r'abrechnung',AbrechnungsViewSet)
router.register(r'dienstmodell',DienstModellViewSet)
router.register(r'wish',WishView)
router.register(r'dienstplan',DienstplanView)
router.register(r'trade',DienstTradeView)

urlpatterns = [
    path('', include(router.urls)),
    path("vw/", admin.site.urls),
    path('userinfo/', check_token),
    path('o/', include('oauth2_provider.urls', namespace='oauth2_provider')),
    path('o/emailtoken/', TokenView.as_view()),
    path('changepassword/',ChangePasswordView.as_view()),
    path('forgotpassword/', forgot_password),
    path('setnewpassword/', set_new_password),
    path('register/', UserRegister.as_view()),
    path('datastore/', include(router.urls)),
    path('dienstplanservice/',dienstplanservice)
]
