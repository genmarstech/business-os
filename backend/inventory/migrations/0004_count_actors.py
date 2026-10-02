"""
Who took the count, as two columns instead of one.

── RENAMED, NOT DROPPED AND RE-ADDED ────────────────────────────────────────
`makemigrations` writes this as three RemoveFields followed by six AddFields,
which is correct about the end state and silently throws away every existing
attribution on the way. Renaming the staff column keeps the counts already
taken pointing at the people who took them; the account column arrives empty
beside it, which is exactly right, because nobody has counted as a subscriber
yet — they could not.

The nullability has to land in the same migration: `opened_by` and
`counted_by` were NOT NULL, and a table with both halves of an either-or pair
required cannot hold a row.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0004_admintotp'),
        ('inventory', '0003_stockcount_stockcountline_and_more'),
        ('organisations', '0025_businessorganization_sector'),
    ]

    operations = [
        migrations.RenameField(
            model_name='stockcount',
            old_name='opened_by',
            new_name='opened_by_staff',
        ),
        migrations.RenameField(
            model_name='stockcount',
            old_name='closed_by',
            new_name='closed_by_staff',
        ),
        migrations.RenameField(
            model_name='stockcountline',
            old_name='counted_by',
            new_name='counted_by_staff',
        ),
        migrations.AlterField(
            model_name='stockcount',
            name='opened_by_staff',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_counts_opened', to='organisations.organizationstaff'),
        ),
        migrations.AlterField(
            model_name='stockcount',
            name='closed_by_staff',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_counts_closed', to='organisations.organizationstaff'),
        ),
        migrations.AlterField(
            model_name='stockcountline',
            name='counted_by_staff',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_count_lines', to='organisations.organizationstaff'),
        ),
        migrations.AddField(
            model_name='stockcount',
            name='opened_by_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_counts_opened', to='identity.platformaccount'),
        ),
        migrations.AddField(
            model_name='stockcount',
            name='closed_by_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_counts_closed', to='identity.platformaccount'),
        ),
        migrations.AddField(
            model_name='stockcountline',
            name='counted_by_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_count_lines', to='identity.platformaccount'),
        ),
        migrations.AddConstraint(
            model_name='stockcount',
            constraint=models.CheckConstraint(condition=models.Q(('opened_by_staff__isnull', True), ('opened_by_account__isnull', True), _connector='OR'), name='stock_count_opened_by_one_actor'),
        ),
        migrations.AddConstraint(
            model_name='stockcount',
            constraint=models.CheckConstraint(condition=models.Q(('closed_by_staff__isnull', True), ('closed_by_account__isnull', True), _connector='OR'), name='stock_count_closed_by_one_actor'),
        ),
        migrations.AddConstraint(
            model_name='stockcountline',
            constraint=models.CheckConstraint(condition=models.Q(('counted_by_staff__isnull', True), ('counted_by_account__isnull', True), _connector='OR'), name='stock_count_line_counted_by_one_actor'),
        ),
    ]
