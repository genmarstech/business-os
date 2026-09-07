from django.db import migrations

def create_groups(apps, schema_editor):
    # Use apps.get_model to avoid importing the model directly
    Group = apps.get_model('auth', 'Group')
    
    roles = [
        'Org admins',
        'Finance',
        'Operations managers',
        'Branch Managers',
        'Inventory managers'
    ]
    
    for role in roles:
        Group.objects.get_or_create(name=role)

def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    roles = ['Org admins', 'Finance', 'Operations managers', 'Branch Managers', 'Inventory managers']
    Group.objects.filter(name__in=roles).delete()

class Migration(migrations.Migration):
    dependencies = [
        # This will automatically point to your previous migration
        ('organisations', '0010_alter_organizationstaff_kra_pin')
    ]

    operations = [
        migrations.RunPython(create_groups, reverse_code=remove_groups),
    ]
