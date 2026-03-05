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
import json
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# إعداد logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class MangaDownloader:
    def __init__(self, base_url, start_chapter, end_chapter, output_dir="downloads", max_workers=3):
        self.base_url = base_url
        self.start = int(start_chapter)
        self.end = int(end_chapter)
        self.output_dir = Path(output_dir)
        self.pdf_dir = self.output_dir / "pdfs"
        self.zip_dir = self.output_dir / "zips"
        self.chapter_urls = []
        self.max_workers = max_workers
        self.cookies_file = Path("cookies.json")  # لحفظ الكوكيز مؤقتاً
        
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.zip_dir.mkdir(parents=True, exist_ok=True)
        
        self.prepare_urls()

    def prepare_urls(self):
        if "{}" in self.base_url:
            for chap_num in range(self.start, self.end + 1):
                url = self.base_url.replace("{}", str(chap_num))
                self.chapter_urls.append((chap_num, url))
        else:
            match = re.search(r'^(.*?)(\d+)$', self.base_url)
            if match:
                base_part = match.group(1)
                for chap_num in range(self.start, self.end + 1):
                    url = base_part + str(chap_num)
                    self.chapter_urls.append((chap_num, url))
            else:
                base_part = self.base_url.rstrip('/') + '/'
                for chap_num in range(self.start, self.end + 1):
                    url = base_part + str(chap_num)
                    self.chapter_urls.append((chap_num, url))
        logging.info(f"تم تجهيز {len(self.chapter_urls)} رابط فصل")

    def extract_images_from_page(self, sb, url):
        logging.info(f"فتح الرابط: {url}")
        
        # استخدام UC mode القوي
        sb.uc_open_with_reconnect(url, reconnect_time=2)
        time.sleep(random.uniform(3, 5))
        
        # حل الكابتشا إذا ظهر
        try:
            sb.uc_gui_click_captcha()
            time.sleep(2)
        except:
            pass
        
        # التأكد من تحميل الصفحة
        try:
            sb.wait_for_element("body", timeout=15)
            logging.info("تم تحميل الصفحة بنجاح")
        except Exception as e:
            logging.error(f"فشل تحميل الصفحة: {e}")
            return []
        
        # التمرير لتحميل الصور
        for _ in range(3):
            sb.execute_script("window.scrollBy(0, 500)")
            time.sleep(1)
        
        # استخراج HTML وتحليله
        html = sb.get_page_source()
        soup = BeautifulSoup(html, 'lxml')
        img_tags = soup.find_all('img')
        logging.info(f"تم العثور على {len(img_tags)} علامة img")
        
        image_urls = []
        for img in img_tags:
            src = img.get('src') or img.get('data-src')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    base = '/'.join(url.split('/')[:3])
                    src = base + src
                image_urls.append(src)
        
        image_urls = list(dict.fromkeys(image_urls))
        filtered_urls = [u for u in image_urls if re.search(r'\.(jpg|jpeg|png|webp)', u.lower())]
        
        if not filtered_urls:
            filtered_urls = image_urls
        
        logging.info(f"تم استخراج {len(filtered_urls)} رابط صورة")
        return filtered_urls

    def get_cookies_safely(self, sb, retries=3):
        """محاولة استخراج الكوكيز بعدة طرق"""
        for attempt in range(retries):
            try:
                # الطريقة الأولى: عبر driver.get_cookies()
                cookies = sb.driver.get_cookies()
                if cookies:
                    logging.info(f"تم استخراج {len(cookies)} كوكيز عبر driver.get_cookies()")
                    return cookies
            except Exception as e:
                logging.warning(f"محاولة {attempt+1} فشلت عبر get_cookies: {e}")
            
            try:
                # الطريقة الثانية: عبر JavaScript
                cookies_js = sb.execute_script("return document.cookie")
                if cookies_js:
                    # تحويل string الكوكيز إلى قائمة
                    cookie_list = []
                    for item in cookies_js.split('; '):
                        if '=' in item:
                            name, value = item.split('=', 1)
                            cookie_list.append({'name': name, 'value': value})
                    if cookie_list:
                        logging.info(f"تم استخراج {len(cookie_list)} كوكيز عبر JavaScript")
                        return cookie_list
            except Exception as e:
                logging.warning(f"محاولة {attempt+1} فشلت عبر JavaScript: {e}")
            
            time.sleep(2)
        
        logging.error("فشل استخراج الكوكيز بعد كل المحاولات")
        return None

    def download_image_with_selenium(self, sb, img_url, img_path):
        """تحميل صورة باستخدام المتصفح مباشرة (خطة بديلة)"""
        try:
            # فتح الصورة في تبويب جديد
            sb.execute_script("window.open(arguments[0], '_blank');", img_url)
            sb.switch_to_window(sb.driver.window_handles[-1])
            time.sleep(2)
            
            # الحصول على محتوى الصورة
            img_base64 = sb.execute_script("""
                var img = document.querySelector('img');
                if (img && img.complete && img.naturalHeight > 0) {
                    var canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    canvas.getContext('2d').drawImage(img, 0, 0);
                    return canvas.toDataURL('image/jpeg').split(',')[1];
                }
                return null;
            """)
            
            # إغلاق التبويب والعودة
            sb.close_window()
            sb.switch_to_window(sb.driver.window_handles[0])
            
            if img_base64:
                import base64
                with open(img_path, 'wb') as f:
                    f.write(base64.b64decode(img_base64))
                return True
        except Exception as e:
            logging.error(f"فشل تحميل الصورة بالسيلينيوم: {e}")
            return False

    def download_images(self, sb, chapter_num, image_urls):
        if not image_urls:
            return None
        
        chapter_dir = self.pdf_dir / f"chapter_{chapter_num:03d}"
        chapter_dir.mkdir(exist_ok=True)
        
        # محاولة استخراج الكوكيز
        cookies = self.get_cookies_safely(sb)
        
        # إعداد جلسة requests مع أو بدون كوكيز
        session = requests.Session()
        if cookies:
            for cookie in cookies:
                session.cookies.set(cookie['name'], cookie['value'])
        
        # إعداد استراتيجية إعادة المحاولة
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://manga-starz.net/',
            'Accept': 'image/avif,image/webp,image/apng,*/*',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
        }
        session.headers.update(headers)
        
        successful_images = []
        
        def download_single_image(img_url, idx):
            # محاولة التحميل بـ requests أولاً
            for attempt in range(2):
                try:
                    time.sleep(random.uniform(0.5, 1.2))
                    response = session.get(img_url, timeout=15)
                    
                    if response.status_code == 200:
                        ext = '.jpg'
                        content_type = response.headers.get('content-type', '')
                        if 'png' in content_type:
                            ext = '.png'
                        elif 'webp' in content_type:
                            ext = '.webp'
                        
                        img_path = chapter_dir / f"{idx:03d}{ext}"
                        with open(img_path, 'wb') as f:
                            f.write(response.content)
                        
                        # التحقق من الصورة
                        Image.open(img_path).verify()
                        return (idx, img_path, 'requests')
                    else:
                        logging.warning(f"محاولة {attempt+1} للصورة {idx} فشلت بكود {response.status_code}")
                except Exception as e:
                    logging.warning(f"محاولة {attempt+1} للصورة {idx} فشلت: {e}")
                
                time.sleep(1)
            
            # إذا فشل requests، جرب بالسيلينيوم
            logging.info(f"محاولة تحميل الصورة {idx} بالسيلينيوم...")
            img_path = chapter_dir / f"{idx:03d}_selenium.jpg"
            if self.download_image_with_selenium(sb, img_url, img_path):
                return (idx, img_path, 'selenium')
            else:
                return (idx, None, None)
        
        # تحميل الصور بالتوازي
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(download_single_image, url, i+1) 
                       for i, url in enumerate(image_urls)]
            
            for future in as_completed(futures):
                idx, path, method = future.result()
                if path:
                    successful_images.append((idx, path, method))
                    logging.debug(f"تم تحميل الصورة {idx} باستخدام {method}")
        
        if not successful_images:
            logging.error(f"لم يتم تحميل أي صور للفصل {chapter_num}")
            return None
        
        # ترتيب الصور
        successful_images.sort(key=lambda x: x[0])
        image_paths = [p for _, p, _ in successful_images]
        
        # إنشاء PDF
        pdf_path = self.pdf_dir / f"chapter_{chapter_num:03d}.pdf"
        self.images_to_pdf(image_paths, pdf_path)
        
        # حذف مجلد الصور
        shutil.rmtree(chapter_dir)
        logging.info(f"PDF للفصل {chapter_num} تم إنشاؤه بـ {len(image_paths)} صورة")
        
        gc.collect()
        return pdf_path

    def images_to_pdf(self, image_paths, output_pdf):
        image_list = []
        for img_path in image_paths:
            try:
                img = Image.open(img_path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                image_list.append(img)
            except Exception as e:
                logging.error(f"خطاء في فتح الصورة {img_path}: {e}")
        
        if image_list:
            image_list[0].save(output_pdf, save_all=True, append_images=image_list[1:])
            for img in image_list:
                img.close()

    def create_zips(self):
        pdf_files = sorted(self.pdf_dir.glob("chapter_*.pdf"))
        if not pdf_files:
            return []
        
        zip_files = []
        for i in range(0, len(pdf_files), 10):
            batch = pdf_files[i:i+10]
            first = int(re.search(r'chapter_(\d+)', batch[0].stem).group(1))
            last = int(re.search(r'chapter_(\d+)', batch[-1].stem).group(1))
            zip_name = self.zip_dir / f"chapters_{first:03d}_to_{last:03d}.zip"
            
            with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zf:
                for pdf in batch:
                    zf.write(pdf, arcname=pdf.name)
            
            zip_files.append(zip_name)
        
        for pdf in pdf_files:
            pdf.unlink()
        
        return zip_files

    def run(self):
        """تشغيل مع جلسة متصفح واحدة"""
        with SB(uc=True, test=True, locale_code="en", headless=True) as sb:
            all_pdfs = []
            for chap_num, url in self.chapter_urls:
                logging.info(f"بدء الفصل {chap_num}")
                
                # استخراج روابط الصور
                image_urls = self.extract_images_from_page(sb, url)
                if not image_urls:
                    logging.error(f"لا توجد صور للفصل {chap_num}")
                    continue
                
                # تحميل الصور
                pdf_path = self.download_images(sb, chap_num, image_urls)
                if pdf_path:
                    all_pdfs.append(pdf_path)
                
                # تنظيف
                gc.collect()
                
                if chap_num < self.end:
                    delay = random.uniform(20, 30)
                    logging.info(f"انتظار {delay:.2f} ثانية...")
                    time.sleep(delay)
        
        # إنشاء ملفات ZIP
        zip_files = self.create_zips()
        logging.info(f"تم إنشاء {len(zip_files)} ملف ZIP")
        return zip_files

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('base_url', help='الرابط الأساسي')
    parser.add_argument('start', type=int)
    parser.add_argument('end', type=int)
    parser.add_argument('--workers', type=int, default=3, help='عدد العمال')
    
    args = parser.parse_args()
    
    downloader = MangaDownloader(args.base_url, args.start, args.end, max_workers=args.workers)
    downloader.run()

if __name__ == "__main__":
    main()
