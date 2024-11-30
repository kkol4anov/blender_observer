import os
import sys
import logging
import json
import ctypes
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pystray import Icon, MenuItem, Menu
from PIL import Image, ImageDraw
import threading
from tkinter import Tk
from tkinter.filedialog import askdirectory
from copy import deepcopy
import plyer
import time
import csv
import subprocess
import files_indexing


format_info="[%(levelname)s] -> %(message)s"
format_debug="[%(levelname)s] [%(filename)s] [%(lineno)d] -> %(message)s"

# logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format=format_debug)
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format=format_info)

monitoring_enabled = True  # Флаг для управления мониторингом
path_to_monitor = os.path.abspath(os.sep)  # Путь по умолчанию
redirectors: dict = {} # Глобальная переменная для редиректоров
observer = None  # Глобальная переменная для наблюдателя
console_visible = True # Глобальная переменная для отображения состояния консоли

path_to_monitor = os.path.normpath(path_to_monitor)

class FileEventHandler(FileSystemEventHandler):
    def __init__(self, filter_func, icon):
        self.filter_func = filter_func
        self.icon = icon
        self.last_deleted = ('', 0)

    def on_moved(self, event):
        global redirectors

        if self.filter_func(event.dest_path):
            old_path = os.path.normpath(event.src_path)
            new_path = os.path.normpath(event.dest_path)
            logging.info(f"Файл переименован: {old_path} -> {new_path}")

            updated = update_redirectors(old_path, new_path, redirectors)

            if not updated:
                return
            
            update_menu(self.icon)

            info_msg = 'Переименован файл библиотеки, ссылки добавлены для исправления'
            logging.info(f'{info_msg}')
            send_notification(info_msg)

            logging.debug(f'{redirectors=}')

    def on_created(self, event):
        global redirectors

        if not event.is_directory and monitoring_enabled:
            src_path = os.path.normpath(event.src_path)
            if self.filter_func(src_path):
                file_name = os.path.basename(src_path)
                if file_name == os.path.basename(self.last_deleted[0]) and time.time() - self.last_deleted[1] < 2:
                    logging.info(f"Файл перемещён: {self.last_deleted[0]} -> {src_path}")
                    
                    updated = update_redirectors(self.last_deleted[0], src_path, redirectors)

                    if not updated:
                        return
                    
                    update_menu(self.icon)

                    info_msg = 'Перемещён файл библиотеки, ссылки добавлены для исправления'
                    logging.info(f'{info_msg}')
                    send_notification(info_msg)

                    logging.debug(f'{redirectors=}')                    

                else:    
                    logging.info(f"Файл создан: {src_path}")

    def on_deleted(self, event):
        if not event.is_directory and monitoring_enabled:
            src_path = os.path.normpath(event.src_path)
            if self.filter_func(src_path):
                file_del_time = time.time()
                self.last_deleted = (src_path, file_del_time)
                logging.info(f"Файл удалён: {src_path}")

    # def on_modified(self, event):
    #     if not event.is_directory and monitoring_enabled:
    #         if self.filter_func(event.src_path):
    #             src_path = os.path.normpath(event.src_path)
    #             logging.info(f"Файл изменён: {src_path}")

def update_redirectors(old_path, new_path, redirectors):
    if redirectors.get(old_path):
        first_path = redirectors[old_path].get('first_path')
    else:
        first_path = old_path

    linkers = get_linkers(first_path)

    if not linkers:
        return
    
    if old_path not in redirectors.keys():
        redirectors[new_path] = {'linkers': linkers, 'first_path': old_path}
    else:
        redirectors[new_path] = redirectors.pop(old_path)

    if new_path == redirectors[new_path].get('first_path'):
        redirectors.pop(new_path)

    return True

def write_redirectors_list(redirectors):
    with open('redirectors_list.csv', 'w', newline='') as csvfile:
        fieldnames = ['new_path', 'old_path', 'linker']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for redirector_key, redirector_value in redirectors.items():
            current_path: str = redirector_key
            first_path: str = redirector_value['first_path']
            blend_files: list = redirector_value['linkers']
            for blend_file in blend_files:
                writer.writerow({'new_path': current_path, 'old_path': first_path, 'linker': blend_file})    

def read_redirectors_list():
    new_redirectors = {}
    with open('redirectors_list.csv', newline='') as csvfile:
        # TODO Удалить исправленные missed_lib через проверку двух два сетов: до и после

        reader = csv.DictReader(csvfile)
        bad_links = False
        warn_msg = 'Ошибка в ссылках. Есть ссылки на несуществующие файлы'
        for row in reader:
            if not os.path.exists(row['new_path']) or not os.path.exists(row['old_path']) or not os.path.exists(row['linker']):
                logging.warning(f"{warn_msg}\nlinker: {row['linker']}\nold_path: {row['old_path']}\nnew_path: {row['new_path']}")
                bad_links = True
            if not new_redirectors.get(row['new_path']):
                new_redirectors[row['new_path']] = {'linkers': [row['linker']], 'first_path': row['old_path']}
            else:
                new_redirectors[row['new_path']]['linkers'].append(row['linker'])
        if bad_links:
            send_notification(warn_msg)


    return new_redirectors

def open_redirectors_editor():
    command = r'"C:\Program Files\WindowsApps\38526MediaLife.CoolOffice_2.1.0.0_x86__1crh1k73ty8mg\program\soffice.exe" redirectors_list.csv'
    subprocess.call(command)

def redirectors_list(icon):
    global redirectors

    write_redirectors_list(redirectors)

    open_redirectors_editor()

    new_redirectors = read_redirectors_list()

    logging.debug(f'{new_redirectors=}')
    redirectors.update(new_redirectors)
    logging.debug(f'{redirectors=}')
    update_menu(icon)
    
    if os.path.exists('redirectors_list.csv'):
        os.remove('redirectors_list.csv')


def hide_console():
    global console_visible

    kernel32 = ctypes.windll.kernel32
    hWnd = kernel32.GetConsoleWindow()

    ctypes.windll.user32.ShowWindow(hWnd, 0)
    console_visible = False

def toggle_console():
    """
    Включает или отключает консольное окно.
    """
    global console_visible
    kernel32 = ctypes.windll.kernel32
    hWnd = kernel32.GetConsoleWindow()
    
    if console_visible:
        # Скрываем консоль
        ctypes.windll.user32.ShowWindow(hWnd, 0)
        console_visible = False
    else:
        # Показываем консоль
        ctypes.windll.user32.ShowWindow(hWnd, 1)
        console_visible = True

# Функция для отправки уведомлений
def send_notification(message):
    if plyer.notification:
        plyer.notification.notify(
            title='Blender Observer',
            message=message,
            timeout=5  # Уведомление будет отображаться 5 секунд
        ) # type: ignore

def create_image():
    """Создаёт простую иконку для трея."""
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 48, 48), fill="blue")
    return image


def toggle_monitoring(icon):
    """Переключение состояния мониторинга."""
    global monitoring_enabled, observer
    monitoring_enabled = not monitoring_enabled

    if monitoring_enabled:
        # Если мониторинг включается, создаём новый объект Observer
        if observer and observer.is_alive():
            logging.info("Наблюдатель уже работает")
            send_notification("Наблюдатель уже работает")
        else:
            observer = start_monitoring(path_to_monitor, filter_func, icon)
            logging.info("Мониторинг запущен")
            send_notification("Мониторинг запущен")
    else:
        # Если мониторинг выключается, корректно останавливаем Observer
        if observer:
            logging.info("Останавливаем мониторинг...")
            observer.stop()
            observer.join()
            observer = None
            logging.info("Мониторинг остановлен")
            send_notification("Мониторинг остановлен")


def set_path(icon):
    """Установить новый путь для мониторинга."""
    global path_to_monitor, observer
    root = Tk()
    root.withdraw()  # Скрыть основное окно
    selected_path = askdirectory(title="Выберите директорию для мониторинга")
    if selected_path:
        path_to_monitor = selected_path
        logging.info(f"Новый путь для мониторинга: {path_to_monitor}")

        # Перезапустить наблюдатель
        if observer:
            logging.info("Перезапускаем наблюдатель для нового пути...")
            observer.stop()
            observer.join()
            observer = start_monitoring(path_to_monitor, filter_func, icon)
            logging.info("Наблюдатель для нового пути перезапущен")

            update_menu(icon)
            files_indexing.update_index(path_to_monitor)
            check_not_existed_libs(icon)

def check_not_existed_libs(icon):
    global redirectors

    index: dict[str, dict] = get_index()
    missed_libraries = index.get('missed_libraries')
    if missed_libraries:
        new_redirectors = {}
        for item_index, item in enumerate(missed_libraries.items()):
            key, value = item
            linkers = value['linkers']
            missed_libraries_linkers = {f'Файл: {linker}, Библиотека: {key}' for linker in linkers}
            new_redirectors[f'missed_lib_{item_index+1}'] = {'linkers': linkers, 'first_path': key}
        
        warn_msg = f"Найдены ссылки на несуществующие библиотеки в {len(missed_libraries_linkers)} файлах."
        logging.warning(warn_msg)
        send_notification(f"{warn_msg} Подробнее в консоли")
        files_str_list = '\n'.join(missed_libraries_linkers)
        logging.warning(f"Список файлов:\n{files_str_list}")

        redirectors.update(new_redirectors)
        update_menu(icon)



def start_monitoring(path, filter_func, icon):
    """Запустить мониторинг указанного пути."""
    event_handler = FileEventHandler(filter_func, icon)
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()
    return observer

def filter_func(file_path):
    """Фильтр для файлов."""
    # Например, добавляем только текстовые файлы
    return file_path.endswith(".blend")

def get_index():
    index_file = 'blend_files_index.json'
    with open(index_file, 'r') as f:
        index = json.load(f)
    return index

# Функция для проверки файла по индексу на наличие зависимых файлов
def get_linkers(filepath):
    index = get_index()
    all_files = index.get('all_files')

    if not all_files or not isinstance(all_files, dict):
        return
    
    file_data = all_files.get(filepath)

    if not file_data or not isinstance(file_data, dict):
        return
    
    linkers = file_data.get('linkers')
    return linkers

def update_links(icon):
    global redirectors, path_to_monitor

    if redirectors:
        redirectors_copy = deepcopy(redirectors)
        for redirector_key, redirector_value in redirectors.items():
            current_path = redirector_key
            if 'missed_lib' in current_path:
                continue
            first_path: str = redirector_value['first_path']
            blend_files: list = redirector_value['linkers']
            for blend_file in blend_files:                
                logging.info(f"Processing file: {blend_file}")
                files_indexing.update_library_path(blend_file, first_path, current_path)
            redirectors_copy.pop(redirector_key)

        redirectors = redirectors_copy
        files_indexing.update_index(path_to_monitor)
        update_menu(icon)

        if len(redirectors) == 0:
            info_msg = 'Ссылки в файлах исправлены'
        else:
            info_msg = 'Не все ссылки в файлах исправлены. Оставшиеся нужно редактировать вручную'

        logging.info(info_msg)
        send_notification(info_msg)

# Меню для трея
def set_tray_menu():
    global path_to_monitor

    menu = Menu(
        MenuItem(
            lambda icon: "Скрыть консоль" if console_visible else "Показать консоль",
            lambda icon: toggle_console()
        ),  # Управление консолью
        MenuItem(
            lambda icon: "Остановить мониторинг" if monitoring_enabled else "Запустить мониторинг",
            lambda icon: toggle_monitoring(icon),
        ),
        MenuItem(f"Текущий путь: {path_to_monitor}", set_path),  # Текущий путь для мониторинга
        MenuItem(f"Исправить ссылки: {len(redirectors.items())}", update_links),
        MenuItem(f"Редактировать список ссылок", redirectors_list),
        MenuItem("Выход", lambda icon: icon.stop())
    )
    return menu

# Функция для обновления текста в меню
def update_menu(icon):
    icon.menu = set_tray_menu()

# Функция для создания и отображения иконки в трее

def create_tray_icon():
    icon = Icon(
        name="File Monitor",
        icon=create_image(),
        menu=set_tray_menu()
    )
    return icon

def main():
    global observer, path_to_monitor

    # Создаём иконку для трея
    
    icon = create_tray_icon()

    observer = start_monitoring(path_to_monitor, filter_func, icon)

    # Запускаем трей в отдельном потоке
    tray_thread = threading.Thread(target=icon.run)
    tray_thread.start()

    hide_console()

    try:
        tray_thread.join()  # Ожидаем завершения трея
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
