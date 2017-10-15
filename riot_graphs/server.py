from flask import Flask
import json

class RiotServer(object):

    @staticmethod
    def hello():
        return "hello"


    def update(self):
        records = self.graphs.push_refresh()
        if records != None:
            status = {'status': 'ok', 'updates': records}
        else:
            status = {'status': 'error'}
        return json.dumps(status)

    def __init__(self, args, graphs):
        self.args = args
        self.graphs = graphs
        self.app = Flask(__name__)
        self.app.add_url_rule('/', 'index', RiotServer.hello)
        self.app.add_url_rule('/update', 'update', self.update)

    def run(self):
        self.app.run(host=self.args['--host'],
                     port=int(self.args['--port']),
                     debug=self.args['--debug'])
