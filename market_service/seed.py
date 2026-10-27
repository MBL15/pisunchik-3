"""Демо-биржа: анкеты участников из демо-ростера и несколько откликов. Даты считаются от `config.today()`."""
from datetime import datetime, time, timedelta

from market_service.models import Offer, PortfolioItem, Profile
from market_service.store import Store
from shared import config
from shared.demo_roster import ROSTER_BY_SEQ, full_name

# (seq, заголовок, о себе, навыки, портфолио[(название, ссылка, описание)], ставка, контакт, доступность, дней назад)
_PROFILES = [
    (3, "Backend и алгоритмы: быстрые сервисы на C++ и Python",
     "Лидер рейтинга сезона по алгоритмике. Беру задачи, где важны скорость и корректность: оптимизация "
     "запросов, поисковые индексы, разбор тяжёлых алгоритмов. Участвовал в командных кейсах ФСП.",
     ["C++", "Python", "Алгоритмы", "PostgreSQL"],
     [("Поисковый индекс для библиотеки ОГУ", "https://github.com/example/library-index", "Полнотекстовый поиск, ускорение в 12 раз"),
      ("Разборы задач ICPC", "https://github.com/example/icpc-notes", "Конспекты и решения по графам и ДП")],
     "от 1 500 ₽/ч", "@sokolov_dev", "OPEN", 12),
    (16, "Продуктовая разработка: от прототипа до MVP",
     "Собираю MVP для небольших команд: интерфейс, API и защита идеи перед заказчиком. "
     "Победитель продуктового трека, опыт командной работы и питчей.",
     ["React", "TypeScript", "FastAPI", "UX", "Pitch"],
     [("Сервис записи в кванториум", "https://example.org/demo/kvantorium", "Демо MVP, 2 недели от идеи до запуска"),
      ("Дизайн-система для школьного портала", "https://example.org/demo/school-ds", "Компоненты и гайдлайны")],
     "договорная", "daria.lebedeva@example.org", "OPEN", 9),
    (19, "Машинное обучение: классификация, CV, NLP",
     "Готовлю данные, обучаю и оцениваю модели, настраиваю метрики. Призёр лидербордов по ИИ. "
     "Могу взять небольшой исследовательский проект или прототип рекомендательной системы.",
     ["Python", "PyTorch", "CV", "NLP", "Pandas"],
     [("Классификатор дефектов на фото", "https://github.com/example/defect-cv", "F1 0.91 на отложенной выборке"),
      ("Чат-бот для абитуриентов", "https://github.com/example/abit-bot", "Поиск по FAQ и документам вуза")],
     "от 2 000 ₽/ч", "@orlov_ml", "BUSY", 6),
    (13, "Аудит безопасности веб-приложений",
     "CTF-практика: поиск уязвимостей в веб-сервисах, разбор логов, закрытие дыр. "
     "Подготовлю отчёт с воспроизведением и рекомендациями по исправлению.",
     ["Web", "Pwn", "Crypto", "Burp Suite"],
     [("Writeup: SQLi на CTF «ОренКод»", "https://example.org/writeups/orencode-sqli", "Разбор цепочки от инъекции до RCE")],
     "по результату", "zaytseva@example.org", "OPEN", 3),
    (28, "Продукт + ИИ: умные функции для сервисов",
     "Совмещаю продуктовую разработку и ML: рекомендации, поиск по смыслу, распознавание. "
     "Ищу небольшие проекты для портфолио, рад обратной связи.",
     ["Python", "Django", "ML", "Docker"],
     [], "обсудим", "@shevchenko_lev", "OPEN", 1),
]

# (профиль seq, отправитель seq, проект, описание, бюджет, контакт, статус, заметка, дней назад)
_OFFERS = [
    (3, 2, "Ускорение выгрузки отчётов реестра",
     "Выгрузка реестра в PDF на 20 тысяч строк занимает минуты. Нужно профилирование и оптимизация запросов.",
     "до 25 000 ₽", "trainer@fsp56.ru", "PENDING", "", 2),
    (3, 5, "Система расписания для технопарка",
     "Нужен сервис расписания кружков с конфликтами аудиторий и уведомлениями родителям.",
     "договорная", "@timofeev_ik", "PENDING", "", 1),
    (16, 3, "Лендинг и личный кабинет турнира",
     "Ищу продуктовую часть для университетского турнира: регистрация команд, таблица результатов.",
     "15 000 ₽", "@sokolov_dev", "ACCEPTED", "Беру, давайте созвонимся на этой неделе", 5),
    (19, 2, "Рекомендации секций для школьников",
     "Подбор секций по интересам ученика. Нужны данные и простая модель.", "", "", "DECLINED",
     "Сейчас занят, вернусь в следующем месяце", 4),
]


def seed(store: Store) -> None:
    now = datetime.combine(config.today(), time(12, 0))
    ids: dict[int, int] = {}
    for seq, headline, about, skills, portfolio, rate, contact, availability, days in _PROFILES:
        person = ROSTER_BY_SEQ[seq]
        store.profile_seq += 1
        at = now - timedelta(days=days)
        store.profiles[store.profile_seq] = Profile(
            id=store.profile_seq, owner_fsp_id=person["fsp_id"], owner_name=full_name(person), headline=headline,
            about=about, skills=skills, portfolio=[PortfolioItem(title=t, url=u, description=d) for t, u, d in portfolio],
            rate=rate, contact=contact, availability=availability, created_at=at, updated_at=at)
        ids[seq] = store.profile_seq
    for owner_seq, sender_seq, title, description, budget, contact, status, note, days in _OFFERS:
        sender = ROSTER_BY_SEQ[sender_seq]
        store.offer_seq += 1
        at = now - timedelta(days=days)
        store.offers[store.offer_seq] = Offer(
            id=store.offer_seq, profile_id=ids[owner_seq], from_fsp_id=sender["fsp_id"], from_name=full_name(sender),
            project_title=title, description=description, budget=budget, contact=contact, status=status, note=note,
            created_at=at, decided_at=at + timedelta(hours=5) if status != "PENDING" else None)
