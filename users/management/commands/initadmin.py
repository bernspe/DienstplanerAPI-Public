import os

import environ
from django.core.management import BaseCommand

from DienstplanerAPI.settings import BASE_DIR
from users.models import User

environ.Env.read_env(os.path.join(BASE_DIR, '.env'))
env = environ.Env(
    # set casting, default value
    DEBUG=(bool, False)
)
class Command(BaseCommand):
    def handle(self, *args, **options):
        if User.objects.count() == 0:
            username = env('DJANGO_SUPERUSER_USERNAME')
            password = env('DJANGO_SUPERUSER_PASSWORD')
            print('Creating account for %s ' % username)
            admin = User.objects.create_superuser(username=username, password=password)
            admin.is_active = True
            admin.is_admin = True
            admin.save()
        else:
            print('Admin accounts can only be initialized if no Accounts exist')