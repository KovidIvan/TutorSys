"""
Формы приложения core для обычного UI деканата.
"""

from django import forms
from .models import Specialty, Department, Group, Curriculum, Discipline, Teacher


class SpecialtyForm(forms.ModelForm):
    class Meta:
        model = Specialty
        fields = ['code', 'name', 'abbr']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например, 6-05-0541-01'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например, Прикладная математика'}),
            'abbr': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например, ПМ'}),
        }


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name', 'head']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Название кафедры'}),
            'head': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['head'].required = False
        self.fields['head'].queryset = Teacher.objects.select_related('department').order_by('full_name')


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ['name', 'specialty', 'course', 'study_form', 'students_count', 'subgroups_count']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например, 26ПМ1д'}),
            'specialty': forms.Select(attrs={'class': 'form-select'}),
            'course': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 6}),
            'study_form': forms.Select(attrs={'class': 'form-select'}),
            'students_count': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'subgroups_count': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['specialty'].queryset = Specialty.objects.order_by('abbr', 'name')


class CurriculumForm(forms.ModelForm):
    class Meta:
        model = Curriculum
        fields = ['specialty', 'admission_year', 'study_form']
        widgets = {
            'specialty': forms.Select(attrs={'class': 'form-select'}),
            'admission_year': forms.NumberInput(attrs={'class': 'form-control', 'min': 1900, 'max': 2100}),
            'study_form': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['specialty'].queryset = Specialty.objects.order_by('abbr', 'name')


class DisciplineForm(forms.ModelForm):
    class Meta:
        model = Discipline
        fields = [
            'name', 'curriculum', 'department', 'semester', 'control_form',
            'hours_lec', 'hours_pract', 'hours_lab', 'hours_usr',
            'control_works', 'credits', 'groups'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Название дисциплины'}),
            'curriculum': forms.Select(attrs={'class': 'form-select'}),
            'department': forms.Select(attrs={'class': 'form-select'}),
            'semester': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 12}),
            'control_form': forms.Select(attrs={'class': 'form-select'}),
            'hours_lec': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'min': 0}),
            'hours_pract': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'min': 0}),
            'hours_lab': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'min': 0}),
            'hours_usr': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'min': 0}),
            'control_works': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'credits': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'min': 0}),
            'groups': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['groups'].required = False
        self.fields['department'].empty_label = '— Не назначена —'
        self.fields['curriculum'].queryset = Curriculum.objects.select_related('specialty').order_by(
            'specialty__abbr', 'specialty__name', '-admission_year', 'study_form'
        )
        self.fields['department'].queryset = Department.objects.order_by('name')
        self.fields['groups'].queryset = Group.objects.select_related('specialty').order_by('course', 'name')
