import os
import json
import time
import requests
from PIL import Image
from PIL.ExifTags import GPSTAGS
import pillow_heif

# 注册 HEIC 格式支持
pillow_heif.register_heif_opener()

PHOTOS_DIR = os.path.join(os.path.dirname(__file__), 'photos')
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), 'footprintData.js')

def get_decimal_from_obj(res, ref):
    """将 EXIF 的度分秒(DMS)转换为十进制经纬度(DD)"""
    try:
        d = float(res[0])
        m = float(res[1])
        s = float(res[2])
        convert_data = d + (m / 60.0) + (s / 3600.0)
        if ref in ['S', 'W']:
            return -convert_data
        return convert_data
    except Exception:
        return None

def extract_gps(image_path):
    """读取照片的 GPS 信息"""
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return None
            gps_ifd = exif.get_ifd(0x8825)
            if not gps_ifd:
                return None

            gps_data = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
            lat_data = gps_data.get('GPSLatitude')
            lat_ref = gps_data.get('GPSLatitudeRef')
            lon_data = gps_data.get('GPSLongitude')
            lon_ref = gps_data.get('GPSLongitudeRef')

            if lat_data and lat_ref and lon_data and lon_ref:
                lat = get_decimal_from_obj(lat_data, lat_ref)
                lon = get_decimal_from_obj(lon_data, lon_ref)
                return lat, lon
    except Exception as e:
        print(f"❌ 解析 EXIF 失败 {os.path.basename(image_path)}: {e}")
    return None

def load_existing_cache():
    """解析现有的 footprintData.js 并建立双重缓存字典"""
    cache_by_image = {}
    area_cache = {}
    
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            
            start_idx = content.find('[')
            end_idx = content.rfind(']') + 1
            if start_idx != -1 and end_idx != -1:
                existing_data = json.loads(content[start_idx:end_idx])
                for item in existing_data:
                    city = item.get("city")
                    country = item.get("country")
                    coords = item.get("coords")
                    
                    for img in item.get("images", []):
                        cache_by_image[img] = {
                            "city": city,
                            "country": country,
                            "coords": coords
                        }
                    
                    if coords and len(coords) == 2:
                        area_key = f"{round(coords[0], 2)},{round(coords[1], 2)}"
                        area_cache[area_key] = {
                            "city": city,
                            "country": country
                        }
                print(f"ℹ️  成功载入历史缓存：包含 {len(cache_by_image)} 张已知照片。")
        except Exception as e:
            print(f"⚠️  读取历史 footprintData.js 失败（将全量重新扫描）: {e}")
            
    return cache_by_image, area_cache

def main():
    if not os.path.exists(PHOTOS_DIR):
        print(f"❌ 错误：找不到 {PHOTOS_DIR} 文件夹！")
        return

    # 加载历史缓存
    cache_by_image, area_cache = load_existing_cache()

    valid_extensions = ('.jpg', '.jpeg', '.heic', '.webp', '.png')
    final_list_of_images = []
    new_photo_data = []

    print("\n🚀 开始扫描 photos 文件夹...")
    # 获取本地当前所有合法的图片，转换为 set 提升比对效率
    current_files = {f for f in os.listdir(PHOTOS_DIR) if f.lower().endswith(valid_extensions)}
    
    # ✨【新增反向检查】如果历史 JS 里的照片在本地找不到了，输出 warning 并从缓存移除
    for cached_img in list(cache_by_image.keys()):
        if cached_img not in current_files:
            print(f"⚠️  Warning: 照片 {cached_img} 在本地 photos 目录中已不存在，已从足迹数据中同步删除。")
            del cache_by_image[cached_img]

    # 正向同步本地现有的照片
    for file in current_files:
        if file in cache_by_image:
            final_list_of_images.append({
                "fileName": file,
                "city": cache_by_image[file]["city"],
                "country": cache_by_image[file]["country"],
                "coords": cache_by_image[file]["coords"]
            })
        else:
            file_path = os.path.join(PHOTOS_DIR, file)
            gps = extract_gps(file_path)
            if gps:
                new_photo_data.append({
                    "fileName": file,
                    "lat": gps[0],
                    "lng": gps[1]
                })
            else:
                print(f"⚠️  跳过（照片无 GPS 卫星定位信息）: {file}")

    if new_photo_data:
        print(f"\n统计：发现 {len(new_photo_data)} 张未记录的新定位照片。")
        print("🌐 开始处理新坐标...")
        
        area_groups = {}
        for photo in new_photo_data:
            group_key = f"{round(photo['lat'], 2)},{round(photo['lng'], 2)}"
            if group_key not in area_groups:
                area_groups[group_key] = {
                    "preciseCoords": [photo['lat'], photo['lng']],
                    "images": []
                }
            area_groups[group_key]["images"].append(photo['fileName'])

        headers = {'User-Agent': 'TravelFootprintGeneratorPython/2.0'}

        for key, group in area_groups.items():
            lat, lng = group["preciseCoords"]
            
            if key in area_cache:
                city = area_cache[key]["city"]
                country = area_cache[key]["country"]
                print(f"⚡ 区域命中有历史缓存: [{city}, {country}] 自动匹配 {len(group['images'])} 张新照片")
            else:
                try:
                    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=10&addressdetails=1&accept-language=en"
                    response = requests.get(url, headers=headers, timeout=10)
                    geo_data = response.json()
                    address = geo_data.get('address', {})

                    country = address.get('country') or "Unknown Country"
                    city = None

                    # 中国四大直辖市特判
                    if country == "China":
                        municipalities = ["Beijing", "Shanghai", "Tianjin", "Chongqing"]
                        state_val = address.get('state', '')
                        muni_val = address.get('municipality', '')
                        city_val = address.get('city', '')
                        
                        for m_name in municipalities:
                            if m_name in state_val or m_name in muni_val or m_name in city_val:
                                city = m_name
                                break

                    if not city:
                        city = (
                            address.get('city') or 
                            address.get('municipality') or 
                            address.get('town') or 
                            address.get('village') or 
                            address.get('county') or 
                            address.get('state_district') or 
                            "Unknown City"
                        )

                    print(f"🗺️  API 成功识别新区域: [{city}, {country}] 包含 {len(group['images'])} 张照片")
                    time.sleep(1)
                except Exception as e:
                    print(f"❌ 地理编码请求失败 [{lat:.2f}, {lng:.2f}]: {e}")
                    city = f"Unknown ({round(lat, 2)})"
                    country = "Unknown"

            for img in group["images"]:
                final_list_of_images.append({
                    "fileName": img,
                    "city": city,
                    "country": country,
                    "coords": [round(lat, 4), round(lng, 4)]
                })
    else:
        print("ℹ️  没有发现任何新照片。")

    # 重新聚合归类
    grouped_result = {}
    for item in final_list_of_images:
        geo_key = (item["city"], item["country"])
        if geo_key not in grouped_result:
            grouped_result[geo_key] = {
                "city": item["city"],
                "country": item["country"],
                "coords": item["coords"],
                "images": []
            }
        grouped_result[geo_key]["images"].append(item["fileName"])

    # 过滤掉那些因为删图而导致“没有任何照片”的空城市节点
    final_footprint_data = [v for v in grouped_result.values() if len(v["images"]) > 0]
    
    output_content = f"// 此文件由 Python 自动化脚本自动生成，请勿手动修改\nconst footprintData = {json.dumps(final_footprint_data, indent=4, ensure_ascii=False)};\n"
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(output_content)
        
    print(f"\n🎉 同步完成！目前足迹共包含 {len(final_footprint_data)} 个城市，全新数据写入至: {OUTPUT_FILE}")

if __name__ == '__main__':
    main()