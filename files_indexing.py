import os
import sys
import json
from pathlib import Path
from blender_asset_tracer import blendfile, bpathlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from time import time
import logging

format_info="[%(levelname)s] -> %(message)s"
format_debug="[%(levelname)s] [%(filename)s] [%(lineno)d] -> %(message)s"

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format=format_debug)

def scan_blend_files(root_dir):
    blend_files_paths = []
    
    # Рекурсивное сканирование директории
    for dirpath, _, filenames in os.walk(root_dir):
        for file in filenames:
            if file.lower().endswith('.blend'):
                # blend_files.append(Path(dirpath) / file)
                blend_files_paths.append(os.path.join(dirpath,file))
    
    return blend_files_paths

def lib_path_rel_to_abs(lib_path, blend_path):
    blend_lib_path = bpathlib.BlendPath(lib_path)
    ref_dirname = os.path.dirname(blend_path).encode(encoding = "utf-8")
    abs_blend_lib_path = blend_lib_path.absolute(ref_dirname)
    absolute_path = os.path.normpath(str(abs_blend_lib_path))
    return absolute_path

def extract_assets(blend_file_path):
    libraries = []
    
    try:
        # Открытие файла .blend
        bf = blendfile.open_cached(Path(blend_file_path))

        libraries = bf.find_blocks_from_code(b'LI')

        # since there is no docs of accepted keys, printing some keys here
        # logging.debug(f'{bf.code_index.keys()=}')
        #[b'REND', b'TEST', b'GLOB', b'WM', b'DATA', b'SC', b'LS', b'OB', b'SN', b'WO', b'BR', b'MA', b'TE', b'LI', b'ID', b'DNA1'])

        # WM = window manager
        # OB = object
        # MA = material
        # BR = brush
        # SC = scene
        # SN = screen
        # DATA = wmWindow
        # LI = library
        
        libraries = [lib_path_rel_to_abs(lib[b'name'], blend_file_path) for lib in libraries]
    
    finally:
        bf.close()

    return libraries

def create_index_chunk(blend_file_path: str):

    index_chunk = {'all_files': {}, 'missed_libraries': {}}

    file_libraries = extract_assets(blend_file_path)
    blend_file_path = os.path.normpath(blend_file_path)

    index_chunk['all_files'][blend_file_path] = {
        'linkers': [],
        'libraries': file_libraries
    }

    for lib in file_libraries:
        if index_chunk['all_files'].get(lib):
            index_chunk['all_files'][lib]['linkers'].append(blend_file_path)
        else:
            index_chunk['all_files'][lib] = {
        'linkers': [blend_file_path],
        'libraries': []
        }
        lib_exists = os.path.exists(lib)
        if not lib_exists:
            if index_chunk['missed_libraries'].get(lib):
                index_chunk['missed_libraries'][lib]['linkers'].append(blend_file_path)
            else:
                index_chunk['missed_libraries'][lib] = {
            'linkers': [blend_file_path],
            }
                
    return index_chunk

def merge_dicts(dicts):
    # Промежуточные defaultdict
    result_all_files = defaultdict(lambda: defaultdict(list))
    result_missed_libraries = defaultdict(lambda: defaultdict(list))
    
    for d in dicts:
        # Обработка all_files
        if "all_files" in d:
            for file_path, file_data in d["all_files"].items():
                for key, value in file_data.items():
                    result_all_files[file_path][key].extend(value)
        
        # Обработка missed_libraries
        if "missed_libraries" in d:
            for lib_path, lib_data in d["missed_libraries"].items():
                for key, value in lib_data.items():
                    result_missed_libraries[lib_path][key].extend(value)
    
    # Преобразуем defaultdict в обычный словарь
    return {
        "all_files": {k: dict(v) for k, v in result_all_files.items()},
        "missed_libraries": {k: dict(v) for k, v in result_missed_libraries.items()}
    }

def process_list_concurrently(worker_function, data, max_workers=4):
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(worker_function, data))
    return results

def create_index_concurrently(root_dir):
    blend_files_paths = scan_blend_files(root_dir)

    s_time = time()
    index_chunks = process_list_concurrently(create_index_chunk, blend_files_paths, max_workers=12)
    e_time = time()
    index = merge_dicts(index_chunks)

    logging.debug(f'{e_time-s_time=:.2f}')

    return index

def create_index(root_dir):
    # index = {'all_files': {}, 'missed_libraries': {}}
    blend_files_paths = scan_blend_files(root_dir)

    start_time_1 = time()
    index_chunks = list(map(create_index_chunk, blend_files_paths))
    end_time_1 = time()

    start_time_2 = time()
    index = merge_dicts(index_chunks)
    end_time_2 = time()

    logging.debug(f'indexing time {end_time_1-start_time_1}')
    logging.debug(f'merging time {end_time_2-start_time_2}')

    # for blend_file_path in blend_files_paths:
    #     file_libraries = extract_assets(blend_file_path)        
    #     blend_file_path: str = os.path.normpath(blend_file_path)

    #     index['all_files'][blend_file_path] = {
    #         'linkers': [],
    #         'libraries': file_libraries
    #     }

    #     for lib in file_libraries:
    #         if index['all_files'].get(lib):
    #             index['all_files'][lib]['linkers'].append(blend_file_path)
    #         else:
    #             index['all_files'][lib] = {
    #         'linkers': [blend_file_path],
    #         'libraries': []
    #         }
    #         lib_exists = os.path.exists(lib)
    #         if not lib_exists:
    #             if index['missed_libraries'].get(lib):
    #                 index['missed_libraries'][lib]['linkers'].append(blend_file_path)
    #             else:
    #                 index['missed_libraries'][lib] = {
    #             'linkers': [blend_file_path],
    #             }
                
    return index

def save_index_to_json(index, output_file):
    with open(output_file, 'w') as f:
        json.dump(index, f, indent=4)

def update_index(root_directory):
    output_json_file = 'blend_files_index.json'
    
    # index = create_index(root_directory)
    index = create_index_concurrently(root_directory)
    save_index_to_json(index, output_json_file)

    logging.info(f"Index saved to {output_json_file}")

def update_library_path(blend_file_path: str, old_library_path: str, new_library_path: str):
    """
    Обновляет путь к библиотеке в .blend-файле.

    Args:
        blend_file_path (str): Путь к .blend-файлу, в котором обновляется библиотека.
        old_library_path (str): Текущий путь к библиотеке, который нужно заменить.
        new_library_path (str): Новый путь для библиотеки.
    """
    # Открываем файл .blend
    bf = blendfile.BlendFile(Path(blend_file_path), "rb+")
    
    try:
        # Ищем все блоки библиотеки (с кодом 'LI')
        libraries = bf.find_blocks_from_code(b'LI')
        
        # Проверяем и обновляем путь к библиотеке
        for lib in libraries:
            lib_name = lib.get(b'name')
            lib_filepath = lib_path_rel_to_abs(lib_name, blend_file_path)
            if lib_filepath == old_library_path:                
                path = bpathlib.BlendPath.mkrelative(
                    Path(new_library_path),
                    bf.filepath
                )
                lib[b'filepath'] = new_library_path  # Устанавливаем новый абсолютный путь
                lib[b"name"] = path # Устанавливаем новый относительный путь
    finally:
        # Закрываем файл
        bf.close()

    # Меняем ссылки в библиотеках
    # print("Changing links...")
    # errors = 0

    # for file in Blend.collection:
    #     print("File:", file.pathname)
    #     try:
    #         blend = blendfile.BlendFile(Path(file.pathname), "rb+")
    #     except:
    #         errors += 1
    #         print("[EXCEPTION] Failed to load")
    #         traceback.print_exc()
    #         continue

    #     try:
    #         for block in blend.find_blocks_from_code(b'LI'):
    #             absolute = bpathlib.BlendPath(block[b"filepath"]).absolute(file.directory.encode(encoding = "utf-8"))
    #             mapping = Alias.get(os.path.relpath(absolute.decode(), directory))
    #             if not mapping: continue
    #             path = bpathlib.BlendPath.mkrelative(
    #                 Path(os.path.join(directory, mapping)),
    #                 blend.filepath
    #             )
    #             if len(path) > 1020:    # 1024
    #                 raise RuntimeError("Path is too long: " + path)
    #             print(f"Changed {block[b'filepath']} to {path}, {len(path)}")
    #             block[b"filepath"] = path
    #             block[b"name"] = path
    #     except:
    #         errors += 1
    #         print("[EXCEPTION] Failed to change path")
    #         traceback.print_exc()


if __name__ == "__main__":
    root_directory = 'testing_dirs'
    update_index(root_directory)