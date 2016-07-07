#!/bin/python3

import os
from io import StringIO
import configparser
import cgi
import logging
import traceback
import argparse

import pyinotify
#import MDRenderer
import mistune
import premailer

import evernote.edam.type.ttypes as Types
import evernote.edam.notestore.NoteStore as NoteStore
import evernote.edam.userstore.constants as UserStoreConstants
from evernote.api.client import EvernoteClient

log = None

class MarkNote(pyinotify.ProcessEvent):
    def __init__(self, work_place, conf=os.path.join(os.path.abspath(os.path.dirname(__file__)), 'conf.ini')):
        try:
            log_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'logs')
            if not os.path.exists(log_path):
                os.mkdir(log_path)
            log_file_path = os.path.join(log_path, 'marknote.log')
            logging.basicConfig(filename=log_file_path)
            global log
            log = logging.getLogger('runtime')
            log.setLevel(logging.INFO)
            
            cf = configparser.SafeConfigParser({'test': 'no',
                                                'account_type': 'evernote',
                                                'auth_token': '',
                                                'MarkDown_style': '',
                                                'log_level': 'info'})
            cf.read(conf)

            log_level = cf.get('main', 'log_level')
            if log_level == 'debug':
                log.setLevel(logging.DEBUG)

            log.info('-'*10 + 'Load the CONF' + '-'*10)

            self._test = True if (cf.get('main','test') == 'yes') else False
            self.account_type = cf.get('main','account_type')
            self.auth_token = cf.get('main','auth_token')
            self.style = cf.get('main','MarkDown_style')
                
            if self._test:
                log.debug("Running in TEST Mode!")

            log.debug('account_type:' + self.account_type)
            log.debug('auth_token:' + self.auth_token)
            log.debug('MarkDown_style:' + self.style)
        except:
            log.error(traceback.format_exc())
            exit(0)
        
        # load css file from ./markdown-css-themes/*style*.css
        css_dir_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'markdown-css-themes')
        css_file = css_dir_path + '/' + self.style + '.css'
        log.debug('Load css content from ' + css_file)
        with open(css_file) as cf:
            self.css = cf.read()
        
        self.work_place = work_place
        # login Evernote & receive user metadata
        self.client = None
        self.user_store = None
        self.note_store = None
        self.login()

        # initialize the markdown renderer
        self.renderer = mistune.Renderer(escape=True, \
                hard_wrap=True, use_xhtml=True)
        self.markdown = mistune.Markdown(renderer=self.renderer)

        # synchronize with online account & receive notebooks metadata
        self.notebooks = {} # {'book_name': guid}
        self.notes = {} # {book_guid: note{title:guid} }
        self.sync_metadata()

        super(pyinotify.ProcessEvent).__init__()

    def login(self):
        # login into the Evernote account
        
        if self._test:
            return True

        try:
            self.client = EvernoteClient(token = self.auth_token)
            self.user_store = self.client.get_user_store()
            version_ok = self.user_store.checkVersion(
                            "Evernote EDAMTest (python)",
                            UserStoreConstants.EDAM_VERSION_MAJOR,
                            UserStoreConstants.EDAM_VERSION_MINOR)
            print("Is my Evernote API version up to date?", str(version_ok))
            print("")
            if not version_ok:
                log.error("Evernote Version check error!")
                return False
            
            self.note_store = self.client.get_note_store()
            log.info('Login succeed')

        except:
            log.debug(traceback.format_exc())
            log.error('Login failed')
            return False

    def sync_metadata(self):
        log.info("SYN_METADATA START")
        # sync the notebooks' metadata with online account
        log.info("Begin sync the notebooks metadata!")
        try:
            notebooks = self.note_store.listNotebooks()
            local_notebooks = []
            for notebook_name in os.listdir(self.work_place):
                if notebook_name[0] == '.':
                    continue
                if os.path.isdir(os.path.join\
                        (self.work_place,notebook_name)):
                    local_notebooks.append(notebook_name)

            for notebook in notebooks:
                if notebook.defaultNotebook:
                    continue
                self.notebooks[notebook.name] = notebook.guid
                if notebook.name in local_notebooks:
                    local_notebooks.remove(notebook.name)
                else:
                    os.mkdir(os.path.join(self.work_place, notebook.name))
                    
            for remain_notebook in local_notebooks:
                self.create_notebook(remain_notebook)

            log.info("Begin sync the notes metadata")
            # sync the notes' metadata with online account
            note_filter = NoteStore.NoteFilter()
        
            for nb_name in self.notebooks:
                nb_guid = self.notebooks[nb_name]
                local_file = {}
                for file_name in os.listdir(os.path.join\
                        (self.work_place, nb_name)):
                    if os.path.isfile(self.work_place + '/' + nb_name +\
                            '/' + file_name):
                        if file_name[0] == '.' or file_name[-1] == '~':
                            continue;
                        else:
                            file_name, file_type = file_name.split('.')
                            local_file[file_name] = file_type
                
                self.notes[nb_guid] = {}
                note_filter.notebookGuid = nb_guid
                notes_list = self.note_store.findNotes(note_filter, 0, 1000)

                for note in notes_list.notes:
                    if not note.active:
                        if note.title in local_file:
                            os.remove(os.path.join(self.work_place, \
                                    nb_name) + '/' + note.title + '.'\
                                    + local_file[note.title])
                            local_file.pop(note.title)
                    else:
                        self.notes[nb_guid][note.title] = note.guid
                        if note.title in local_file:
                            file_path = os.path.join(self.work_place, \
                                    nb_name) + '/' + note.title + '.'\
                                    + local_file[note.title]
                            if os.path.getmtime(file_path) > note.updated:
                                content = ''
                                with open(file_path, 'r') as f:
                                    content = file.read()
                                
                                if local_file[note.title] == 'md':
                                    content = self.markdown2html(content)
                                else:
                                    content = self.text2html(content)
                                
                                self.update_note(note.title, content,\
                                        nb_name)
                            
                            local_file.pop(note.title)
                        else:
                            content, _type = self.get_note_content(note.guid)
                            file_path = os.path.join(self.work_place, \
                                    nb_name) + '/' + note.title + '.' \
                                    + _type
                            with open(file_path, 'x') as f:
                                f.write(content)

                for _name in local_file:
                    _type = local_file[name]
                    file_path = os.path.join(self.work_place, nb_name)\
                            + '/' + _name + '.' + _type

                    content = ''
                    with open(file_path, 'r') as f:
                        content = file.read()
                    if _type == 'md':
                        content = self.markdown2html(content)
                    else:
                        content = self.text2html(content)
                    
                    self.create_note(_name, content, nb_name)

            log.info("SYNC_METADATA SUCCEED!")
        except:
            log.error(traceback.format_exc())
            log.error("sync notebooks metadata failed!")
            return False

    def get_note_content(self, note_guid):
        found_note = self.note_store.getNoteContent(note_guid)
        found_note = found_note[found_note.find('!!!type'):]
        found_note = found_note[:found_note.find('/<div>')]
        _type = found_note[8:found_note.find(':epyt!!!')]
        content = found_note[found_note.find(':epyt!!!')+ 8:\
                found_note.find('</div>')]
        return content, _type

    def markdown2html(self, content):
        html = '<style>' + self.css +'</style>'
        html += '<article class="markdown-body">'
        html += self.markdown(content)
        html += '</article>'

        log.debug("inline css begin!")

        prem = premailer.Premailer(html, preserve_inline_attachments=False, base_path='article')
        html = prem.transform(pretty_print=True)

        html = html[html.find('<article'):]
        html = html[html.find('>')+1:]
        html = html[:html.find('</article>')]
        
        log.debug("inline css over")
        
        html += '<div style="display:none">'
        html += '!!!type:md:epyt!!!'
        html += content
        html += '</div>'

        return html

    def text2html(self, content):
        
        content.replace('\r', '')
        html = ""
        lines = content.split('\n')
        for line in lines:
            lstr = '<div>'
            if not line:
                lstr += '<br />'
            else:
                lstr += cgi.escape(line)
            lstr += '</div>'
            html += lstr

        html += '<div style="display:none">'
        html += "!!!type:txt:epyt!!!"
        html += content
        html += '</div>'
        return html
    
    def note_producer(self, title, content, notebook_guid, guid=None):
        note = Types.Note()

        note.title = title
        if guid:
            note.guid = guid

        note.content = '<?xml version="1.0" encoding="UTF-8"?>'
        note.content += '<!DOCTYPE en-note SYSTEM \
                "http://xml.evernote.com/pub/enml2.dtd">'
        note.content += '<en-note>'
        note.content += content
        note.content += '</en-note>'
        note.notebookGuid = notebook_guid
        return note

    def create_note(self, title, content, notebook_name):
        log.info('create note with title {0} under notebook {1}'\
                .format(title, notebook_name))
        if notebook_name in self.notebooks:
            notebook_guid = self.notebooks[notebook_name]
        else:
            log.debug('Error in create_note! The notebook named {0}\
                    is not exist!'.format(notebook_name))
            return False

        if title in self.notes[notebook_guid]:
            log.debug('The file with title {0} exists!'.format(title))
            print('The file with title {0} exists!'.format(title))
            return True

        note = self.note_producer(title, content, notebook_guid)
        
        created_note = self.note_store.createNote(note)
        
        self.notes[notebook_guid][created_note.title] = created_note.guid
        log.debug("Create note successfully!")
        return True

    def update_note(self, title, content, notebook_name):
        log.info('Update the note with title {0} under notebook {1}'.\
                format(title, notebook_name))

        if notebook_name in self.notebooks:
            notebook_guid = self.notebooks[notebook_name]
        else:
            log.debug('Error in create_note! The notebook named {0}\
                    is not exist!'.format(notebook_name))
            return False
        
        note_guid = self.notes[notebook_guid][title]
        note = self.note_producer(title, content, notebook_guid, note_guid)
        updated_note = self.note_store.updateNote(note)

        return True

    def delete_note(self, title, notebook_name):
        log.debug('Begin delete a file title {0} under notebook {1}'.\
                format(title, notebook_name))

        notebook_guid = self.notebooks[notebook_name]
        note_guid = self.notes[notebook_guid][title]
        try:
            deleted_note = self.note_store.expungeNote(note_guid)
        except:
            log.debug(traceback.format_exc())
            log.error('Delete note fail!')
            return False
        self.notes[notebook_guid].pop(title, 0)
        log.debug('Delete note succeed!')
        return True
    
    def create_notebook(self, notebook_name):
        log.debug('Begin create notebook with name ' + notebook_name)

        notebook = Types.Notebook()
        notebook.defaultNotebook = False
        notebook.name = notebook_name
        created_notebook = self.note_store.createNotebook(notebook)
        log.debug('Create notebook succeed!')
        self.notebooks[notebook_name] = created_notebook.guid
        self.notes[created_notebook.guid] = {}
        return True
    
    def process_IN_CREATE(self, event):
        log.info("Create:{0} {1}".format(event.path, event.name))

        if event.dir:
            notebook_name = event.name
            if not self.create_notebook(notebook_name):
                log.debug(traceback.format_exc())
                log.error("Create notebook fail!")
            else:
                log.debug("Create notebook"+notebook_name+"succeed!")
        else:
            notebook_name = event.path.split('/')[-1]
            note_name = event.name
            if note_name[0] == '.' or note_name[-1] == '~' \
                    or note_name == '4913':
                log.debug("Temp file! Ignore it!")
            
            else:
                content = ''
                with open(event.path+'/'+event.name, 'r') as f:
                    content = f.read()
                
                note_name, note_type = note_name.split('.')
                if note_type == 'md':
                    content = self.markdown2html(content)
                else:
                    content = self.text2html(content)
                
                self.create_note(note_name, content, notebook_name)
                log.info("Create note" + note_name + "finished!")

    def process_IN_CLOSE_WRITE(self, event):
        note_name = event.name
        notebook_name = event.path.split('/')[-1]
        log.info('Finding file{0} under {1} being modified'.\
                format(note_name, notebook_name))

        if note_name[0] == '.' or note_name[-1] == '~' \
                or note_name == '4913':
            log.debug('Temp file! ignore!')

        else:
            content = ''
            with open(event.path + '/' + event.name, 'r') as f:
                content = f.read()

            note_name, note_type = note_name.split('.')
            if note_type == 'md':
                content = self.markdown2html(content)
            else:
                content = self.text2html(content)
            
            self.update_note(note_name, content, notebook_name)

            log.info('Update note finished!')

    def process_IN_DELETE(self, event):
        log.info('Finding file {0} being deleted under notebook {1}'.\
                format(event.name, event.path.split('/')[-1]))

        if event.dir:
            log.debug('....To be continue')
            return 

        else:
            note_name = event.name
            if note_name[0] == '.' or note_name[-1] == '~' \
                    or note_name == '4913':
                log.debug('Temp file! ignore!')
                return
            
            notebook_name = event.path.split('/')[-1]
            note_name, note_type = note_name.split('.')
            self.delete_note(note_name, notebook_name)
            
            log.info('Deleting note finished!')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-w', '--work-place', help='the path of workspace',\
            required=True)

    args = parser.parse_args()
    work_place = args.work_place

    wm = pyinotify.WatchManager()
    handler = MarkNote(work_place)
    notifier = pyinotify.Notifier(wm, default_proc_fun=handler)
    mask = pyinotify.IN_CREATE|pyinotify.IN_DELETE|pyinotify.IN_CLOSE_WRITE

    wm.add_watch(work_place, mask, rec=True)

    notifier.loop()

if __name__ == '__main__':
    main()
