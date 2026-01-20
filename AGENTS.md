# This is my Student's Assignment checker script

It using Gemini API to determine the score.

Here's the main criteria of this script:
- This project is using `uv` as the python package manager and builder
- Focus on scoring the student answer which are in PDF or `.doc` format
- Avoid scoring the question file. It usually marked (or included) in the filename like `question` or `Question`.
- Here's the example of the student answer path pattern: `StudentAnswer_ISYS6599038_DFEA_LEC_Personal_Assignment_2_19.01.2026.19.16/2902737810_LUK SEKAR DADARI/TP2-ISYS6599 – MIS for Leader 2025_LUK SEKAR DADARI_2902737810.pdf`, `StudentAnswer_ISYS6599038_DFEA_LEC_Personal_Assignment_2_19.01.2026.19.16/2502142961_HILMY MAULANA ABID/TP2-ISYS6599 – MIS for Leader 2025.docx`, or `StudentAnswer_ISYS6599038_DFEA_LEC_Personal_Assignment_2_19.01.2026.19.16/2902730514_ANISA FIKRI HANIFAH/Assigment 2 - MIS - W7 - Anisa Fikri Hanifah.pdf`
- The markdown file is for the custom prompt.
- This checker should be dynamic, because the number of the questions need to be reviewed can be differ for each week.