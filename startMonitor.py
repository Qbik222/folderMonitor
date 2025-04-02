import os
import threading
import time
import json
import re  # Імпортуємо модуль для роботи з регулярними виразами
from tkinter import Tk, Label, Entry, Button, StringVar, messagebox
import firebase_admin
from firebase_admin import credentials, db

# Файл для збереження налаштувань
CONFIG_FILE = "config.json"

# Ініціалізація Firebase
firebase_app = None
database_ref = None

def load_config():
    """Завантажує налаштування з файлу config.json."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as file:
            return json.load(file)
    return {}

def save_config(config):
    """Зберігає налаштування у файл config.json."""
    with open(CONFIG_FILE, "w") as file:
        json.dump(config, file)

def initialize_firebase(url, key_path):
    global firebase_app, database_ref
    try:
        if firebase_app:
            firebase_admin.delete_app(firebase_app)

        cred = credentials.Certificate(key_path)
        firebase_app = firebase_admin.initialize_app(cred, {'databaseURL': url})
        database_ref = db.reference("/")  # Коренева директорія RTDB
        messagebox.showinfo("Успіх", "Firebase (Realtime Database) ініціалізовано успішно!")
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося ініціалізувати Firebase: {e}")

def monitor_directory(directory_path):
    print(f"🔍 Моніторинг директорії: {directory_path}")
    known_folders = {}
    while True:
        time.sleep(5)
        current_folders = set(os.listdir(directory_path))
        new_folders = current_folders - set(known_folders.keys())
        
        # Перевіряємо нові папки
        for folder in new_folders:
            if is_valid_folder_name(folder):
                write_to_file(folder)  # Записуємо дані у файл
                send_to_firebase_from_file()  # Відправляємо дані з файлу в базу даних
                known_folders[folder] = set(os.listdir(os.path.join(directory_path, folder)))
        
        # Перевіряємо зміни у відомих папках
        for folder, known_files in known_folders.items():
            folder_path = os.path.join(directory_path, folder)
            current_files = set(os.listdir(folder_path))
            new_files = current_files - known_files
            if new_files:
                print(f"🆕 У папці '{folder}' знайдено нові файли: {new_files}")
                write_to_file(folder)  # Записуємо дані у файл
                send_to_firebase_from_file()  # Відправляємо дані з файлу в базу даних
                known_folders[folder] = current_files

def is_valid_folder_name(folder_name):
    """Перевіряє, чи назва папки відповідає формату '123.456'."""
    pattern = r"^\d{3}\.\d{3}$"
    return re.match(pattern, folder_name) is not None

def write_to_file(folder_name):
    """Записує дані у файл data.txt."""
    try:
        with open("data.txt", "a") as file:
            file.write(f"{folder_name}\n")
        print(f"✅ Дані про папку '{folder_name}' записані у файл data.txt.")
    except Exception as e:
        print(f"❌ Помилка при записі у файл: {e}")

def send_to_firebase_from_file():
    """Читає дані з файлу data.txt і відправляє їх у Firebase."""
    global database_ref
    if database_ref:
        try:
            with open("data.txt", "r") as file:
                lines = file.readlines()
            
            # Очищаємо файл після зчитування
            open("data.txt", "w").close()

            for line in lines:
                folder_name = line.strip()
                # Додаємо кожну назву папки як окремий запис у Firebase
                new_entry_ref = database_ref.child("text_entries").push()  # push() створює унікальний ключ
                new_entry_ref.set({
                    'value': folder_name,  # Зберігаємо назву папки як текстове значення
                    'timestamp': time.time()  # Додаємо часову мітку для відстеження
                })
                print(f"✅ Назва папки '{folder_name}' успішно відправлена в Realtime Database.")
        except Exception as e:
            print(f"❌ Помилка при передачі в Firebase: {e}")
    else:
        print("⚠️ Firebase не ініціалізовано.")

# Графічний інтерфейс
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Firebase Folder Monitor")

        # Завантажуємо налаштування з файлу
        self.config = load_config()

        self.firebase_url = StringVar(value=self.config.get("firebase_url", ""))
        self.firebase_key_path = StringVar(value=self.config.get("firebase_key_path", ""))
        self.directory_path = StringVar(value=self.config.get("directory_path", ""))

        Label(root, text="Посилання на Firebase:").grid(row=0, column=0, sticky="w")
        Entry(root, textvariable=self.firebase_url, width=50).grid(row=0, column=1)
        Button(root, text="Зберегти URL", command=self.save_url).grid(row=0, column=2)

        Label(root, text="Шлях до ключа Firebase:").grid(row=1, column=0, sticky="w")
        Entry(root, textvariable=self.firebase_key_path, width=50).grid(row=1, column=1)
        Button(root, text="Зберегти ключ", command=self.save_key).grid(row=1, column=2)

        Label(root, text="Моніторинг директорії:").grid(row=2, column=0, sticky="w")
        Entry(root, textvariable=self.directory_path, width=50).grid(row=2, column=1)
        Button(root, text="Почати моніторинг", command=self.start_monitoring).grid(row=2, column=2)

    def save_url(self):
        url = self.firebase_url.get()
        if url:
            self.config["firebase_url"] = url
            save_config(self.config)
            messagebox.showinfo("Успіх", f"URL Firebase збережено: {url}")
        else:
            messagebox.showwarning("Попередження", "Введіть URL Firebase.")

    def save_key(self):
        key_path = self.firebase_key_path.get()
        if os.path.isfile(key_path):
            self.config["firebase_key_path"] = key_path
            save_config(self.config)
            messagebox.showinfo("Успіх", f"Ключ Firebase збережено: {key_path}")
        else:
            messagebox.showwarning("Попередження", "Файл ключа не знайдено.")

    def start_monitoring(self):
        print("🔄 Спроба запустити моніторинг...")
        directory = self.directory_path.get()
        url = self.firebase_url.get()
        key_path = self.firebase_key_path.get()

        if not directory or not os.path.isdir(directory):
            print("❌ Директорія не валідна!")
            return

        try:
            self.config["directory_path"] = directory
            save_config(self.config)
            initialize_firebase(url, key_path)
            thread = threading.Thread(target=monitor_directory, args=(directory,), daemon=True)
            thread.start()
            print("✅ Моніторинг запущено!")
        except Exception as e:
            print(f"❌ Помилка: {e}")

if __name__ == "__main__":
    root = Tk()
    app = App(root)
    root.mainloop()