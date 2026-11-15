from decimal import Decimal

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from .models import CalcSettings, JournalEntry, LoadItem, PositionRule


class CalcSettingsForm(forms.ModelForm):
    class Meta:
        model = CalcSettings
        fields = (
            'exam_hours_per_student',
            'exam_consult_hours_per_group',
            'credit_hours_per_student',
            'usr_control_hours_per_group',
            'kr_hours_per_student',
            'subgroups_per_group',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self._meta.model._meta.get_field(name).verbose_name
            field.validators.append(MinValueValidator(0))
        subgroup_field = self.fields['subgroups_per_group']
        subgroup_field.validators.extend((MinValueValidator(1), MaxValueValidator(10)))


class PositionRuleForm(forms.ModelForm):
    class Meta:
        model = PositionRule
        fields = ('min_hours', 'max_hours', 'min_lecture_hours', 'can_lecture')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('min_hours', 'max_hours', 'min_lecture_hours'):
            self.fields[name].validators.append(MinValueValidator(0))

    def clean(self):
        cleaned_data = super().clean()
        min_hours = cleaned_data.get('min_hours')
        max_hours = cleaned_data.get('max_hours')
        min_lecture_hours = cleaned_data.get('min_lecture_hours')
        if min_hours is not None and max_hours is not None and min_hours > max_hours:
            self.add_error('min_hours', 'Минимум часов не может превышать максимум.')
        if max_hours is not None and min_lecture_hours is not None and min_lecture_hours > max_hours:
            self.add_error('min_lecture_hours', 'Минимум лекционных часов не может превышать максимум.')
        return cleaned_data


class AddPositionForm(forms.Form):
    position = forms.CharField(max_length=100, label='Должность')
    min_hours = forms.IntegerField(min_value=0, initial=840, label='Минимум часов')
    max_hours = forms.IntegerField(min_value=0, initial=900, label='Максимум часов')
    min_lecture_hours = forms.IntegerField(min_value=0, initial=200, label='Минимум лекционных часов')
    can_lecture = forms.BooleanField(required=False, label='Может читать лекции')

    def clean_position(self):
        position = self.cleaned_data['position'].strip()
        if PositionRule.objects.filter(position__iexact=position).exists():
            raise forms.ValidationError('Должность с таким названием уже существует.')
        return position

    def clean(self):
        cleaned_data = super().clean()
        min_hours = cleaned_data.get('min_hours')
        max_hours = cleaned_data.get('max_hours')
        min_lecture_hours = cleaned_data.get('min_lecture_hours')
        if min_hours is not None and max_hours is not None and min_hours > max_hours:
            self.add_error('min_hours', 'Минимум часов не может превышать максимум.')
        if max_hours is not None and min_lecture_hours is not None and min_lecture_hours > max_hours:
            self.add_error('min_lecture_hours', 'Минимум лекционных часов не может превышать максимум.')
        return cleaned_data


class JournalEntryForm(forms.ModelForm):
    hours = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_value=Decimal('24'),
        max_digits=5,
        decimal_places=2,
        label='Затрачено часов'
    )

    class Meta:
        model = JournalEntry
        fields = ('load_item', 'date', 'time', 'hours', 'topic')
        labels = {
            'load_item': 'Элемент нагрузки',
            'date': 'Дата проведения',
            'time': 'Время начала',
            'topic': 'Тема занятия',
        }

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['load_item'].queryset = (
            LoadItem.objects.filter(teacher=teacher).select_related('discipline', 'group')
            if teacher is not None else LoadItem.objects.none()
        )
