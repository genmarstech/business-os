from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0018_alter_organizationstaff_branch'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationstaff',
            name='external_user_id',
            field=models.UUIDField(
                unique=True,
                db_index=True,
                editable=False,
                null=True,
                blank=True,
                help_text='External identity from company backend',
            ),
        ),
    ]