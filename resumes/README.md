# resumes/

Базовые резюме — по одному на семейство ролей × язык. Имена файлов:
`<family_id>_<ru|en>.tex`, где `family_id` — из `profile.json` → `role_families[].id`.

Это **не** финальные отклики. Отклик собирается скиллом `tailor-cv` из ближайшего
базового резюме в `applications/<дата>_<компания>_<роль>/` — база при этом не меняется.

Собираются скиллом `build-resume`. Вручную:

```bash
pdflatex -interaction=nonstopmode -halt-on-error backend_ru.tex
pdflatex -interaction=nonstopmode -halt-on-error backend_ru.tex   # второй проход
rm -f *.aux *.log *.out
```

Второй проход нужен для корректных ссылок и переносов. PDF стоит держать в git:
это то, что реально уходит работодателю, и его удобно смотреть в истории.
