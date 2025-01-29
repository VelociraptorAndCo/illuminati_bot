import logging

import os
from datetime import date
from random import choices as randchoices
import yaml
import pandas as pd
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

HW_NUM, HW_FILE, HW_QUESTION = range(3)
CH_NUM, CH_DAY, CH_STUD = range(3)

class IlluminatiBot:
    common_commands = {
        'start': 'Запустить бота и авторизоваться по спискам',
        'help': 'Получить список доступных команд',
        'contacts': 'Получить список сотрудников курса с обьяснением ролей',
    }
    commands = {
        'admin':{
            **common_commands,
            'add_day date': 'Добавить дату очередного прошедшего дня занятий'\
                'и распределить проверяющих\n (если без даты, то сегодня)',
            # not implemented
            'check_hw': 'Проверить домашние задания за определенный день'

        },
        'student':{
            **common_commands,
            'homework': 'Сдать домашнюю работу.\n'\
                'Также можно сразу ввести номер домашнего задания после имени команды'\
                ' или использовать сокращенный вариант\n к примеру: /hw 3',
        },
    }
    def __init__(self):
        self.hwdir = 'homeworks'
        self.students = 'students.csv'
        self.admins = 'assistants.csv'# Роли могут быть Куратор, Преподаватель или Ассистент
        self.lessons_passed = 0
        self.lessons = []

        if not os.path.exists(self.hwdir):
            os.makedirs(self.hwdir)
        if not os.path.exists(self.students):
            pd.DataFrame(columns=['Имя', 'Ник']).to_csv(self.students, index=False)
        else:
            last_col_name = pd.read_csv(self.students).columns[-1]
            if last_col_name[0] in '0123456789':
                self.lessons_passed = int(last_col_name.split('_', 1)[0])
        if not os.path.exists(self.admins):
            pd.DataFrame(columns=['Имя', 'Ник', 'Роль']).to_csv(self.admins, index=False)

        self.hw_ids = pd.DataFrame(
                index=pd.read_csv(self.students)['Имя'],
                columns=range(1, self.lessons_passed+1)
                )

        with open("config.yaml", "r") as file:
            token = yaml.safe_load(file)['token']
        application = Application.builder().token(token).build()
        self.application = application

        # Add start and help commands handler
        start_handler = CommandHandler("start", self.start)
        application.add_handler(start_handler)
        help_handler = CommandHandler("help", self.help)
        application.add_handler(help_handler)
        contacts_handler = CommandHandler("contacts", self.contacts)
        application.add_handler(contacts_handler)

        day_handler = CommandHandler("add_day", self.add_day)
        application.add_handler(day_handler)

        # Add conversation handler
        hw_handler = ConversationHandler(
            entry_points=[CommandHandler(["homework", "hw"], self.hw_start)],
            states={
                HW_NUM: [
                    MessageHandler(
                        filters.Regex("^[0-9]+$"), self.hw_num
                    ),
                ],
                HW_FILE: [
                    MessageHandler(
                        filters.Document.ALL , self.hw_file
                    ),
                ],
                HW_QUESTION: [
                    MessageHandler(
                        filters.TEXT & ~(filters.COMMAND | filters.Regex("^😄$")), self.hw_question,
                    ),
                    MessageHandler(
                        filters.Regex("^😄$") & ~(filters.COMMAND), self.hw_end,
                    )
                ],
            },
            fallbacks=[CommandHandler('cancel', self.hw_cancel)],
        )
        application.add_handler(hw_handler)

        ch_handler = ConversationHandler(
            entry_points=[CommandHandler(["check_homework", "check_hw"], self.ch_start)],
            states={
                CH_NUM: [
                    MessageHandler(
                        filters.Regex("^[0-9]+$"), self.ch_num
                    ),
                ],
                CH_DAY: [
                    MessageHandler(
                        filters.Regex("^[0-9]+$"), self.ch_day
                    ),
                    CommandHandler('next', self.ch_next),
                    CommandHandler('task', self.ch_task),
                ],
                CH_STUD: [
                    MessageHandler(
                        filters.TEXT & ~(filters.COMMAND), self.ch_stud,
                    ),
                    CommandHandler('next', self.ch_next),
                    CommandHandler('task', self.ch_task),
                    CommandHandler('all', self.ch_all),
                ],
            },
            fallbacks=[CommandHandler('cancel', self.ch_cancel)],
        )
        application.add_handler(ch_handler)

    def update_hw_ids_index(self):
        if(len(pd.read_csv(self.students)) != len(self.hw_ids)) or (
            (pd.read_csv(self.students)['Имя'] != self.hw_ids.index).any() ):
            self.hw_ids = pd.merge(
                pd.read_csv(self.students)['Имя'].to_frame().set_index('Имя'),
                self.hw_ids,
                left_index=True,
                right_index=True
                     )

    def run(self):
        """Run the bot until the user presses Ctrl-C"""
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        username = '@' + update.effective_chat.username
        name = update.effective_chat.first_name
        student_df = pd.read_csv(self.students)
        admin_df = pd.read_csv(self.admins)
        if (username == student_df['Ник']).any():
            context.user_data['auth'] = 'student'
            context.user_data['num'] = student_df.query(f"Ник == '{username}'").index[0]
            message = (
            'Ты, похоже, учащийся курса)\n' 
            f'Добро пожаловать, {name}!\n' 
            'Этого бота можно использовать для сдачи домашнего задания, ' 
            'а также для получения списка контактов преподавателей и их ассистентов.\n'
            'Желаем приятного обучения!')
        elif (username == admin_df['Ник']).any():
            context.user_data['auth'] = 'admin'
            message = (
            'Ты, похоже, тут главный)\n'
            f'Добро пожаловать, {name}!\n'
            f'У тебя роль {admin_df.loc[admin_df['Ник']==username, 'Роль'].item()}.\n'
            'Этот бот тут для помощи тебе).')
        else:
            if len(admin_df) > 0:
                curator = admin_df.query('Роль == "Куратор"').iloc[0]
            else:
                curator = {'Имя':'Имя', 'Ник':'Ник'}
            message = (
            f'Мы не смогли найти тебя в списках, {name}!\n'
            'Если такого не должно быть, то просим написать куратору курса '
            f'{curator['Имя']}:{curator['Ник']}')
        await update.message.reply_text(message)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if 'auth' not in context.user_data:
            await update.message.reply_text(
                                      'Сначала выполните команду /start для авторизации'
                                      )
        elif context.user_data['auth'] == 'admin':
            await update.message.reply_text(
                f'список команд для администратора:\n' +
                '\n'.join(f'/{command}:\n {descr}' for command, descr in self.commands['admin'].items())
            )
        elif context.user_data['auth'] == 'student':
            await update.message.reply_text(
                f'список команд для студента:\n' +
                '\n'.join(f'/{command}:\n {descr}' for command, descr in self.commands['student'].items())
            )
        else:
            print(f"user @{update.effective_user.username} has undetected role {context.user_data['auth']}")
            await update.message.reply_text('Role not recognized')

    async def contacts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        admins_df = pd.read_csv(self.admins)
        message = 'Прошу любить и жаловать участвующих в организации курса)\n\n'
        for group, df in admins_df.groupby('Роль', sort=False):
            if group == 'Куратор':
                message += 'Куратор, отвечающий за организацию курса:\n'
            elif group == 'Преподаватель':
                message += 'Любимые преподаватели:\n'
            elif group == 'Ассистент':
                message += 'Ассистенты, всегда рады ответить на любой вопрос, а еще проверяют домашки:\n'
            else:
                message += f'Я не уверен, но написано {group}:\n'
            message += '\n'.join(row['Имя']+': '+row['Ник'] for _, row in df.iterrows())+'\n'
        message += 'Ну и я, скромный бот:\n@'+update.get_bot().username
        await update.message.reply_text(message)

    async def add_day(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data['auth'] != 'admin':
            await update.message.reply_text('Похоже вы пытаетесь сделать что-то не то...')
            return
        if not context.args:
            date_to_add = date.today().strftime("%d-%m-%Y")
        else:
            date_to_add = context.args[0]

        self.lessons.append(date_to_add)
        self.lessons_passed = len(self.lessons)
        student_df = pd.read_csv(self.students)
        assistants = pd.read_csv(self.admins).query("Роль == 'Ассистент'")['Ник'].to_list()
        if not assistants: assistants = [None]
        self.update_hw_ids_index()
        self.hw_ids.loc[:, str(self.lessons_passed)] = None
        n = str(self.lessons_passed)
        path = os.path.join(self.hwdir, str(n))
        if not os.path.exists(path):
            os.makedirs(path)

        student_df.loc[:, 'day_'+n+'_'+date_to_add] = None
        student_df.loc[:, 'day_'+n+'_hw_path'] = None
        student_df.loc[:, 'day_'+n+'_inspector'] = \
            randchoices(assistants, k = len(student_df))
        student_df.loc[:, 'day_'+n+'_comment'] = None
        student_df.loc[:, 'day_'+n+'_mark'] = None
        student_df.to_csv(self.students, index=False)

        await update.message.reply_text(f'Добавлена дата {date_to_add}')

    async def hw_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Starts the homework handling."""
        if context.user_data['auth'] != 'student':
            await update.message.reply_text('Похоже вы пытаетесь сделать что-то не то...')
            return ConversationHandler.END

        if context.args:
            context.user_data['hw_num'] = int(context.args[0])
            await update.message.reply_text(
                'Отлично!\n'\
                f'Похоже ты хочешь сдать домашнее задание {context.user_data['hw_num']}.\n'\
                'Готов принять твой файл)',
            )
            return HW_FILE
        else:
            reply_keyboard = [[str(b) for b in range(max(self.lessons_passed - 3, 1), self.lessons_passed + 1)]]
            await update.message.reply_text(
                'Отлично!'\
                'Пришли, пожалуйста, номер домашнего задания, которое хочешь сдать.\n'\
                '(можешь просто написать, а не клавиатуру нажимать)',
                reply_markup=ReplyKeyboardMarkup(
                            reply_keyboard, one_time_keyboard=True,
                            )
                )
            return HW_NUM

    async def hw_num(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Записывает номер дз и отправляется ждать файл"""
        context.user_data['hw_num'] = int(update.message.text)
        await update.message.reply_text(
        'Отлично!\n'\
        f'Похоже ты хочешь сдать домашнее задание {context.user_data['hw_num']}.\n'\
        'Готов принять твой файл)'
        )
        return HW_FILE

    async def hw_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет файл в указанную папку и отмечает в таблице путь до него."""
        student_df = pd.read_csv(self.students)
        hw_ids_df = self.hw_ids
        row = context.user_data['num']
        col = f'day_{context.user_data['hw_num']}_hw_path'
        name = student_df.loc[row, 'Имя']
        path = os.path.join(self.hwdir, f'{context.user_data['hw_num']}')
        # качаем в path файл и добавляем к названию имя студента
        file = await update.message.document.get_file()
        filename = file.file_path.replace('\\', '/').split('/')[-1]
        filepath = os.path.join(path, name + '_' + filename)
        await file.download_to_drive(filepath)
        student_df.loc[row, col] = filepath
        hw_ids_df.loc[name, context.user_data['hw_num']] = file.file_id
        student_df.to_csv(self.students, index=False)
        insp_col = f'day_{context.user_data['hw_num']}_inspector'
        inspector = '' if not student_df.loc[row, insp_col] else \
            f'''Проверять будет {student_df.loc[row, insp_col]}\n'''
        await update.message.reply_text(
            'Файл получен)\n'+
            inspector+
            'Можешь задать вопрос или написать комментарий в следующем сообщении, \n'\
            'иначе нажми кнопку на клавиатуре для завершения.',
            reply_markup=ReplyKeyboardMarkup(
                [['😄']], one_time_keyboard=True,
            )
        )
        return HW_QUESTION

    async def hw_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Записывает вопрос или комментарий в таблицу"""
        student_df = pd.read_csv(self.students)
        row = context.user_data['num']
        col = f'day_{context.user_data['hw_num']}_comment'
        comment = update.message.text
        student_df.loc[row, col] = comment
        student_df.to_csv(self.students, index=False)
        await update.message.reply_text(
        f"""Проверяющий увидит твой комментарий)""")
        return await self.hw_end(update, context)

    async def hw_end(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Завершение приема дз."""
        await update.message.reply_text(
        f"Домашнее задание {context.user_data['hw_num']} принято.\n"\
        "Удачи с дальнейшим обучением)")
        del context.user_data['hw_num']
        return ConversationHandler.END

    async def hw_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancels and ends the homework handling."""
        user = update.message.from_user
        logger.info("Student %s canceled hw handling.", user.first_name)
        await update.message.reply_text(
            "Отмена приема задания.", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    def ch_get_all(self, assistant: str, context_data: dict) -> str:
        day = context_data['hw_num']
        assistant = '@'+assistant
        to_check = pd.read_csv(self.students).query(f"day_{day}_inspector == '{assistant}'")
        amount = len(to_check)
        not_handled = to_check.loc[to_check[f"day_{day}_hw_path"].isna()]
        handled = to_check.loc[to_check[f"day_{day}_hw_path"].notna()]
        to_check = handled.loc[handled[f"day_{day}_mark"].isna()]
        context_data['to_check'] = to_check
        context_data['to_check_set'] = {idx for idx in to_check.index}

        message = f'Всего за этот день необходимо проверить {amount} работ, причем {len(not_handled)} еще не сдано\n'\
                  f'Сейчас осталось проверить {len(to_check)} заданий:\n'\
                  '\n'.join(f'{n} '+row['Имя'] for n, row in to_check.iterrows())
        return message

    async def ch_get_stud(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        stud_num = context.user_data['stud_num']
        stud = pd.read_csv(self.students).loc[stud_num]
        day = context.user_data['hw_num']
        text = f'{stud['Имя']}: {stud['Ник']}'
        if stud[f'day_{day}_comment']:
            text +='\n Комментарий:\n' + stud[f'day_{day}_comment']
        await update.message.reply_text(text)
        try:
            file = self.hw_ids.loc[stud['Имя'], day]
            if file is None: raise KeyError
        except KeyError:
            file = stud[f'day_{day}_hw_path']
        file_id = await update.message.reply_document(file)
        self.hw_ids.loc[stud['Имя'], day] = file_id
        return CH_STUD

    async def ch_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Starts the homework checking."""
        if context.user_data['auth'] != 'admin':
            await update.message.reply_text('Похоже вы пытаетесь сделать что-то не то...')
            return ConversationHandler.END

        if context.args:
            context.user_data['hw_num'] = int(context.args[0])
            assistant = update.message.from_user.username
            await update.message.reply_text(
                'Отлично!\n' \
                f'Начинаем проверку дз номер {context.user_data['hw_num']}.\n\n'+
                self.ch_get_all(assistant, context.user_data),
            )
            return CH_DAY
        else:
            return await self.ch_task(update, context)

    async def ch_num(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data['hw_num'] = int(update.message.text)
        assistant = update.message.from_user.username
        await update.message.reply_text(
            'Отлично!\n'\
            f'Начинаем проверку дз номер {context.user_data['hw_num']}.\n\n'+
                self.ch_get_all(assistant, context.user_data),
        )
        return CH_DAY

    async def ch_day(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        stud_num = int(update.message.text)
        if stud_num not in context.user_data['to_check_set']:
            text = 'Введен неверный номер, пожалуйста введите верный или /next'
            await update.message.reply_text(text)
            return CH_DAY
        context.user_data['stud_num'] = stud_num
        return await self.ch_get_stud(update, context)

    async def ch_stud(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        student_df = pd.read_csv(self.students)
        row = context.user_data['stud_num']
        col = f'day_{context.user_data['hw_num']}_mark'
        mark = update.message.text
        student_df.loc[row, col] = mark
        student_df.to_csv(self.students, index=False)
        context.user_data['to_check_set'].remove(row)
        await update.message.reply_text(
            f"""Оценка записана""")
        return await self.ch_next(update, context)

    async def ch_next(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        s = context.user_data['to_check_set']
        num = s.pop()
        s.add(num)
        context.user_data['stud_num'] = num
        return await self.ch_get_stud(update, context)

    async def ch_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        reply_keyboard = reply_keyboard = [[str(b) for b in range(max(self.lessons_passed - 3, 1), self.lessons_passed + 1)]]
        await update.message.reply_text(
            'Пришли номер дз, которое хочешь проверять.\n',
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, one_time_keyboard=True,
            )
        )
        return CH_NUM

    async def ch_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        assistant = update.message.from_user.username
        await update.message.reply_text(
            self.ch_get_all(assistant, context.user_data),
        )
        return CH_DAY

    async def ch_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        return ConversationHandler.END

if __name__ == '__main__':
    all_seeing_eye = IlluminatiBot()
    print('inited')
    all_seeing_eye.run()