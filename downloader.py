import os
import re
import sys
import gc
import zipfile
import requests
from PIL import Image
from seleniumbase import SB
import argparse
import logging
from pathlib import Path
import time
import shutil
import random
from bs4 import BeautifulSoup

# إعداد logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class MangaDownloader:
    def __init__(self, base_url, start_chapter, end_chapter, output_dir="downloads"):
        self.base_url = base_url
        self.start = int(start_chapter)
        self.end = int(end_chapter)
        self.output_dir = Path(output_dir)
        self.pdf_dir = self.output_dir / "pdfs"
        self.zip_dir = self.output_dir / "zips"
        self.chapter_urls = []
        
        # إنشاء المجلدات
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.zip_dir.mkdir(parents=True, exist_ok=True)
        
        # تجهيز روابط الفصول
        self.prepare_urls()

    def prepare_urls(self):
        """توليد روابط الفصول بناءً على الرابط الأساسي والمدى"""
        if "{}" in self.base_url:
            # حالة وجود placeholder
            for chap_num in range(self.start, self.end + 1):
                url = self.base_url.replace("{}", str(chap_num))
                self.chapter_urls.append((chap_num, url))
        else:
            # حالة الرابط المباشر (بدون {}). نحاول استخراج الجزء الأساسي من الرابط
            match = re.search(r'^(.*?)(\d+)$', self.base_url)
            if match:
                base_part = match.group(1)  # الجزء قبل الرقم
                for chap_num in range(self.start, self.end + 1):
                    url = base_part + str(chap_num)
                    self.chapter_urls.append((chap_num, url))
            else:
                # إذا لم نتمكن من استخراج الرقم، نضيف الرقم إلى نهاية الرابط مع / إذا لزم الأمر
                base_part = self.base_url.rstrip('/') + '/'
                for chap_num in range(self.start, self.end + 1):
                    url = base_part + str(chap_num)
                    self.chapter_urls.append((chap_num, url))
        
        logging.info(f"تم تجهيز {len(self.chapter_urls)} رابط فصل")
        logging.debug(f"الروابط: {self.chapter_urls}")

    def extract_images_from_page(self, url):
        """استخراج روابط الصور من صفحة الفصل بنفس الطريقة الناجحة مع arabshentai.com"""
        with SB(uc=True, test=True, locale_code="en") as sb:
            logging.info(f"فتح الرابط: {url}")
            
            # تفعيل وضع CDP وفتح الرابط
            sb.activate_cdp_mode(url)
            
            # انتظار تحميل الصفحة
            logging.info("انتظار تحميل الصفحة...")
            sb.sleep(random.uniform(3, 5))
            
            # محاولة حل Cloudflare / Turnstile captcha
            try:
                logging.info("محاولة حل الكابتشا...")
                sb.uc_gui_click_captcha()
                sb.sleep(2)
            except Exception as e:
                logging.debug(f"لا يوجد كابتشا أو فشل النقر: {e}")
            
            # انتظار إضافي بعد حل الكابتشا
            sb.sleep(random.uniform(2, 4))
            
            # التأكد من تحميل الصفحة
            try:
                sb.assert_element("body", timeout=10)
                logging.info("تم تأكيد تحميل الصفحة بنجاح")
            except Exception as e:
                logging.error(f"فشل تحميل الصفحة: {e}")
                return []
            
            # تمييز بعض العناصر (لمحاكاة السلوك البشري)
            try:
                sb.highlight("a", loops=1)
            except:
                pass
            
            # التمرير لأسفل لتحميل الصور (إذا كانت lazy loading)
            for _ in range(3):
                sb.execute_script("window.scrollBy(0, 400)")
                sb.sleep(1)
            
            # انتظار ظهور الصور
            sb.sleep(2)
            
            # استخراج HTML
            html = sb.get_page_source()
            
            # استخدام BeautifulSoup لاستخراج جميع الصور
            soup = BeautifulSoup(html, 'lxml')
            
            # استخراج جميع عناصر img
            img_tags = soup.find_all('img')
            logging.info(f"تم العثور على {len(img_tags)} علامة img في HTML")
            
            image_urls = []
            for img in img_tags:
                # محاولة الحصول على src أو data-src
                src = img.get('src') or img.get('data-src')
                if src:
                    # معالجة الروابط النسبية
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif src.startswith('/'):
                        base = '/'.join(url.split('/')[:3])
                        src = base + src
                    image_urls.append(src)
            
            # إزالة التكرارات
            image_urls = list(dict.fromkeys(image_urls))
            
            # تصفية الروابط التي تحتوي على كلمات مفتاحية للإعلانات أو الأيقونات
            filtered_urls = []
            for img_url in image_urls:
                # تجاهل الصور الصغيرة أو الإعلانات
                if any(keyword in img_url.lower() for keyword in ['logo', 'icon', 'banner', 'ad', 'sponsor']):
                    continue
                # الاحتفاظ بالروابط التي تبدو كصور مانجا (غالباً jpg, png, webp)
                if re.search(r'\.(jpg|jpeg|png|webp|gif)(\?|$)', img_url.lower()):
                    filtered_urls.append(img_url)
            
            if not filtered_urls:
                logging.warning("لم يتم العثور على صور مانجا، قد يكون هناك خطأ في التصفية")
                filtered_urls = image_urls  # استخدام الكل كخطة احتياطية
            
            logging.info(f"تم استخراج {len(filtered_urls)} رابط صورة بعد التصفية")
            
            # إظهار رسالة نجاح
            try:
                sb.post_message(f"تم استخراج {len(filtered_urls)} صورة للفصل", duration=2)
            except:
                pass
            
            return filtered_urls

    def download_images(self, chapter_num, image_urls):
        """تحميل الصور وإنشاء PDF"""
        if not image_urls:
            logging.error(f"لا توجد صور للفصل {chapter_num}")
            return None
        
        chapter_dir = self.pdf_dir / f"chapter_{chapter_num:03d}"
        chapter_dir.mkdir(exist_ok=True)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://manga-starz.net/',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
        }
        
        images = []
        for idx, img_url in enumerate(image_urls, 1):
            try:
                logging.debug(f"تحميل الصورة {idx}/{len(image_urls)} للفصل {chapter_num}")
                
                # إضافة تأخير عشوائي بين الطلبات
                time.sleep(random.uniform(0.5, 1.5))
                
                response = requests.get(img_url, headers=headers, timeout=15)
                response.raise_for_status()
                
                img_path = chapter_dir / f"{idx:03d}.jpg"
                with open(img_path, 'wb') as f:
                    f.write(response.content)
                
                # فتح الصورة للتأكد من صحتها
                img = Image.open(img_path)
                images.append(img_path)
                
                # تنظيف بعد كل صورة
                del response
                
            except Exception as e:
                logging.error(f"فشل تحميل الصورة {img_url}: {e}")
                continue
        
        if not images:
            logging.error(f"لم يتم تحميل أي صور للفصل {chapter_num}")
            return None
        
        # إنشاء PDF
        pdf_path = self.pdf_dir / f"chapter_{chapter_num:03d}.pdf"
        self.images_to_pdf(images, pdf_path)
        
        # حذف مجلد الصور بعد إنشاء PDF
        shutil.rmtree(chapter_dir)
        logging.info(f"تم إنشاء PDF للفصل {chapter_num}: {pdf_path}")
        
        # تنظيف الذاكرة
        del images
        gc.collect()
        
        return pdf_path

    def images_to_pdf(self, image_paths, output_pdf):
        """تحويل قائمة الصور إلى ملف PDF واحد"""
        image_list = []
        for img_path in image_paths:
            img = Image.open(img_path)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            image_list.append(img)
        
        # حفظ كـ PDF
        if image_list:
            image_list[0].save(output_pdf, save_all=True, append_images=image_list[1:])
        
        # إغلاق الصور
        for img in image_list:
            img.close()

    def create_zips(self):
        """تجميع ملفات PDF في zip كل 10 فصول"""
        pdf_files = sorted(self.pdf_dir.glob("chapter_*.pdf"))
        
        if not pdf_files:
            logging.warning("لا توجد ملفات PDF للضغط")
            return []
        
        # تجميع كل 10 فصول
        zip_files = []
        for i in range(0, len(pdf_files), 10):
            batch = pdf_files[i:i+10]
            first_chap = int(re.search(r'chapter_(\d+)', batch[0].stem).group(1))
            last_chap = int(re.search(r'chapter_(\d+)', batch[-1].stem).group(1))
            zip_name = self.zip_dir / f"chapters_{first_chap:03d}_to_{last_chap:03d}.zip"
            
            with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zf:
                for pdf in batch:
                    zf.write(pdf, arcname=pdf.name)
            
            logging.info(f"تم إنشاء {zip_name}")
            zip_files.append(zip_name)
        
        # حذف ملفات PDF بعد الضغط
        for pdf in pdf_files:
            pdf.unlink()
        
        logging.info("تم حذف ملفات PDF الأصلية")
        return zip_files

    def run(self):
        """تشغيل عملية التحميل"""
        all_pdfs = []
        for chap_num, url in self.chapter_urls:
            logging.info(f"بدء معالجة الفصل {chap_num}")
            
            # استخراج روابط الصور
            image_urls = self.extract_images_from_page(url)
            
            if not image_urls:
                logging.error(f"فشل استخراج الصور للفصل {chap_num}، تخطي...")
                continue
            
            # تحميل وإنشاء PDF
            pdf_path = self.download_images(chap_num, image_urls)
            if pdf_path:
                all_pdfs.append(pdf_path)
            
            # تنظيف الذاكرة بعد كل فصل
            gc.collect()
            
            # تأخير عشوائي بين الفصول
            if chap_num < self.end:
                delay = random.uniform(10, 20)
                logging.info(f"انتظار {delay:.2f} ثانية قبل الفصل التالي...")
                time.sleep(delay)
        
        # إنشاء ملفات zip
        zip_files = self.create_zips()
        logging.info(f"تم إنشاء {len(zip_files)} ملف zip بنجاح")
        
        return zip_files

def main():
    parser = argparse.ArgumentParser(description='تحميل مانجا من manga-starz.net')
    parser.add_argument('base_url', help='الرابط الأساسي. إذا كان يحتوي على {} سيتم استبداله برقم الفصل، وإلا سيتم إضافة رقم الفصل إلى نهاية الرابط.')
    parser.add_argument('start', type=int, help='رقم فصل البداية')
    parser.add_argument('end', type=int, help='رقم فصل النهاية')
    
    args = parser.parse_args()
    
    if "{}" not in args.base_url:
        logging.warning("الرابط لا يحتوي على {}، سيتم افتراض أن الرابط هو للفصل الأول وإضافة الأرقام إلى نهايته.")
    
    downloader = MangaDownloader(args.base_url, args.start, args.end)
    downloader.run()

if __name__ == "__main__":
    main()
