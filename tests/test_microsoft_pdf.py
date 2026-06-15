import shutil, fitz   # pip install pymupdf

src = "Microsoft-Sample-Invoice.pdf"
dst = "Invoice-TAMPERED.pdf"
shutil.copyfile(src, dst)              # never edit the original

doc = fitz.open(dst)
page = doc[0]
# add a fake amount so BOTH the content stream and the text layer change
page.insert_text((100, 700), "Total: 50,000", fontsize=12, color=(0, 0, 0))
doc.save(dst, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
doc.close()
print("wrote", dst)