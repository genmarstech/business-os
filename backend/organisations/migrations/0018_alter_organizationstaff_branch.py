from django.db import migrations, models
import django.db.models.deletion


def empty_branch_to_null(apps, schema_editor):
    OrganizationStaff = apps.get_model(
        'organisations',
        'OrganizationStaff'
    )

    OrganizationStaff.objects.filter(branch='').update(branch=None)


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0017_alter_businessorganization_name'),
        ('branches', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            empty_branch_to_null,
            migrations.RunPython.noop,
        ),

        migrations.AlterField(
            model_name='organizationstaff',
            name='branch',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='organization_staff',
                to='branches.branches',
            ),
        ),
    ]