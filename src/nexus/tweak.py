import os
import yaml

import logging; logger = logging.getLogger(__name__)

#TODO: Write a save function for Tweak objects output as YAML configFile but using TweakModule objects

class Tweak():
    ''' Handles configuration and logs of configs for
        the entire server/processing pipeline.
    '''

    def __init__(self, configFile=None):
        cwd = os.getcwd()
        if configFile is None:
            # Going with default config
            self.configFile = cwd+'/basic_demo.yaml'
        else:
            # Reading config from other yaml file
            self.configFile = cwd+'/'+configFile
        
        self.modules = {}
        self.connections = {}

    def __repr__(self):
        return str(self.__dict__)
        
    def createConfig(self):
        ''' Read yaml config file and create config for Nexus
            TODO: check for config file compliance, error handle it
        '''
        with open(self.configFile, 'r') as ymlfile:
            cfg = yaml.safe_load(ymlfile)

        for name,module in cfg['modules'].items(): 
            # put import/name info in TweakModule object TODO: make ordered?
            packagename = module.pop('package')
            classname = module.pop('class')
            if len(module)>0:
                options = module
            tweakModule = TweakModule(name, packagename, classname, options=module)

            if "GUI" in name:
                self.hasGUI = True
                self.gui = tweakModule

            self.modules.update({name:tweakModule})
        
        #print('self.modules:  ', self.modules)

        for name,conn in cfg['connections'].items():
            #TODO check for correctness  TODO: make more generic (not just q_out)
            self.connections.update({name:conn}) #conn should be a list
        
        #print('self.connections: ', self.connections)

    def addParams(self, type, param):
        ''' Function to add paramter param of type type
        '''

    def saveConfig(self):
        #remake cfg TODO
        cfg = self.modules
        yaml.safe_dump(cfg)


class TweakModule():
    def __init__(self, name, packagename, classname, options=None):
        self.name = name
        self.packagename = packagename
        self.classname = classname
        self.config_from_file = {}

        if 'config_file' in options:  # Module-specific config file
            config_file = options.pop('config_file')
            with open(config_file) as f:
                self.config_from_file = yaml.load(f)

        self.options = options

    def __str__(self):
        return f'{self.name}, {self.options}, {self.config_from_file}'


if __name__ == '__main__':
    t = Tweak('../basic_demo.yaml')
    t.createConfig()
