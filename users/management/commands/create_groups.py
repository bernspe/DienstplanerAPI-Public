"""
Create permission groups
Create permissions (read only) to models for a set of groups
"""
# g=Group.objects.all()
# to get the perms from the shell use [str(gg).replace(' | ','.').replace(' ','_').lower() for gg in g[0].permissions.all()]

import logging

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission

grouppermdict = {
    'Basic':['auth.view_permission',
             'oauth2_provider.add_accesstoken',
             'oauth2_provider.delete_accesstoken',
             'oauth2_provider.view_accesstoken'],
    'Reader': ['auth.view_permission',
              'oauth2_provider.add_accesstoken',
              'oauth2_provider.delete_accesstoken',
              'oauth2_provider.view_accesstoken',
              'dienstplan.view_dienstplandata'],
    'Arzt': ['auth.view_permission',
             'oauth2_provider.add_accesstoken',
             'oauth2_provider.delete_accesstoken',
             'oauth2_provider.view_accesstoken',
             'dienstplan.add_wishdata',
             'dienstplan.change_wishdata',
             'dienstplan.delete_wishdata',
             'dienstplan.view_wishdata',
             'dienstplan.view_dienstplandata',
             'users.change_user',
             'users.delete_user',
             'users.view_user'],
    'Planer': ['auth.view_permission',
               'oauth2_provider.add_accesstoken',
               'oauth2_provider.delete_accesstoken',
               'oauth2_provider.view_accesstoken',
               'dienstplan.add_wishdata',
               'dienstplan.change_wishdata',
               'dienstplan.delete_wishdata',
               'dienstplan.view_wishdata',
               'dienstplan.view_dienstplandata',
               'dienstplan.add_dienstplandata',
               'dienstplan.change_dienstplandata',
               'dienstplan.delete_dienstplandata',
               'users.change_user',
               'users.delete_user',
               'users.view_user'],
    'Verwaltung': ['auth.view_permission',
                   'oauth2_provider.add_accesstoken',
                   'oauth2_provider.delete_accesstoken',
                   'oauth2_provider.view_accesstoken',
                   'dienstplan.add_wishdata',
                   'dienstplan.change_wishdata',
                   'dienstplan.delete_wishdata',
                   'dienstplan.view_wishdata',
                   'dienstplan.view_dienstplandata',
                   'users.change_user',
                   'users.delete_user',
                   'users.view_user',
                   'users.add_dienstmodell',
                   'users.add_institution',
                   'users.change_dienstmodell',
                   'users.change_institution',
                   'users.delete_dienstmodell',
                   'users.delete_institution',
                   'users.view_dienstmodell',
                   'users.view_institution',
                   ]

}


class Command(BaseCommand):
    help = 'Creates read only default permission groups for users'

    def handle(self, *args, **options):
        for k, v in grouppermdict.items():
            new_group, created = Group.objects.get_or_create(name=k)
            for permission in v:
                app_label = permission.split('.')[0]
                model_name = permission.rsplit('_')[-1]
                permstring = permission.split('.')[1]
                print('Getting Content Type for app {} and model {} with permstring {}'.format(app_label, model_name,
                                                                                               permstring))
                content_type = ContentType.objects.get(app_label=app_label, model=model_name)
                print("Creating {}".format(permission))
                try:
                    model_add_perm = Permission.objects.get(codename=permstring, content_type=content_type)
                except Permission.DoesNotExist:
                    logging.warning("**** Permission not found with name '{}'.".format(permission))
                    continue

                new_group.permissions.add(model_add_perm)

        print("Created default group and permissions.")
