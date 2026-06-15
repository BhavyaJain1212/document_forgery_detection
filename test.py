from pdf_forgery.font_forensics import analyze_path, render_summary

report = analyze_path("test_pdf's/tampered.pdf")
print(render_summary(report))