"use strict";
var LemmaClient = (() => {
  var __create = Object.create;
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __getProtoOf = Object.getPrototypeOf;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __typeError = (msg) => {
    throw TypeError(msg);
  };
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
  var __commonJS = (cb, mod) => function __require() {
    try {
      return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
    } catch (e) {
      throw mod = 0, e;
    }
  };
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
    // If the importer is in node compatibility mode or this is not an ESM
    // file that has been converted to a CommonJS file using a Babel-
    // compatible transform (i.e. "__esModule" has not been set), then set
    // "default" to the CommonJS "module.exports" for node compatibility.
    isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
    mod
  ));
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);
  var __accessCheck = (obj, member, msg) => member.has(obj) || __typeError("Cannot " + msg);
  var __privateGet = (obj, member, getter) => (__accessCheck(obj, member, "read from private field"), getter ? getter.call(obj) : member.get(obj));
  var __privateAdd = (obj, member, value) => member.has(obj) ? __typeError("Cannot add the same private member more than once") : member instanceof WeakSet ? member.add(obj) : member.set(obj, value);
  var __privateSet = (obj, member, value, setter) => (__accessCheck(obj, member, "write to private field"), setter ? setter.call(obj, value) : member.set(obj, value), value);

  // node_modules/supertokens-website/lib/build/utils/windowHandler/defaultImplementation.js
  var require_defaultImplementation = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/windowHandler/defaultImplementation.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.defaultWindowHandlerImplementation = void 0;
      function getWindowOrThrow() {
        if (typeof window === "undefined") {
          throw Error(
            "If you are using this package with server-side rendering, please make sure that you are checking if the window object is defined."
          );
        }
        return window;
      }
      var defaultLocalStorageHandler = {
        key: function(index) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().localStorage.key(index)];
            });
          });
        },
        clear: function() {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().localStorage.clear()];
            });
          });
        },
        getItem: function(key) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().localStorage.getItem(key)];
            });
          });
        },
        removeItem: function(key) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().localStorage.removeItem(key)];
            });
          });
        },
        setItem: function(key, value) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().localStorage.setItem(key, value)];
            });
          });
        },
        keySync: function(index) {
          return getWindowOrThrow().localStorage.key(index);
        },
        clearSync: function() {
          return getWindowOrThrow().localStorage.clear();
        },
        getItemSync: function(key) {
          return getWindowOrThrow().localStorage.getItem(key);
        },
        removeItemSync: function(key) {
          return getWindowOrThrow().localStorage.removeItem(key);
        },
        setItemSync: function(key, value) {
          return getWindowOrThrow().localStorage.setItem(key, value);
        }
      };
      var defaultSessionStorageHandler = {
        key: function(index) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().sessionStorage.key(index)];
            });
          });
        },
        clear: function() {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().sessionStorage.clear()];
            });
          });
        },
        getItem: function(key) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().sessionStorage.getItem(key)];
            });
          });
        },
        removeItem: function(key) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().sessionStorage.removeItem(key)];
            });
          });
        },
        setItem: function(key, value) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, getWindowOrThrow().sessionStorage.setItem(key, value)];
            });
          });
        },
        keySync: function(index) {
          return getWindowOrThrow().sessionStorage.key(index);
        },
        clearSync: function() {
          return getWindowOrThrow().sessionStorage.clear();
        },
        getItemSync: function(key) {
          return getWindowOrThrow().sessionStorage.getItem(key);
        },
        removeItemSync: function(key) {
          return getWindowOrThrow().sessionStorage.removeItem(key);
        },
        setItemSync: function(key, value) {
          return getWindowOrThrow().sessionStorage.setItem(key, value);
        }
      };
      exports.defaultWindowHandlerImplementation = {
        history: {
          replaceState: function(data, unused, url) {
            return getWindowOrThrow().history.replaceState(data, unused, url);
          },
          getState: function() {
            return getWindowOrThrow().history.state;
          }
        },
        location: {
          getHref: function() {
            return getWindowOrThrow().location.href;
          },
          setHref: function(href) {
            getWindowOrThrow().location.href = href;
          },
          getSearch: function() {
            return getWindowOrThrow().location.search;
          },
          getHash: function() {
            return getWindowOrThrow().location.hash;
          },
          getPathName: function() {
            return getWindowOrThrow().location.pathname;
          },
          assign: function(url) {
            getWindowOrThrow().location.assign(url);
          },
          getHostName: function() {
            return getWindowOrThrow().location.hostname;
          },
          getHost: function() {
            return getWindowOrThrow().location.host;
          },
          getOrigin: function() {
            return getWindowOrThrow().location.origin;
          }
        },
        getDocument: function() {
          return getWindowOrThrow().document;
        },
        getWindowUnsafe: function() {
          return getWindowOrThrow().window;
        },
        localStorage: defaultLocalStorageHandler,
        sessionStorage: defaultSessionStorageHandler
      };
    }
  });

  // node_modules/supertokens-website/lib/build/utils/windowHandler/index.js
  var require_windowHandler = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/windowHandler/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.WindowHandlerReference = void 0;
      var defaultImplementation_1 = require_defaultImplementation();
      var WindowHandlerReference = (
        /** @class */
        (function() {
          function WindowHandlerReference2(windowHandlerInput) {
            var windowHandlerFunc = function(original) {
              return original;
            };
            if (windowHandlerInput !== void 0) {
              windowHandlerFunc = windowHandlerInput;
            }
            this.windowHandler = windowHandlerFunc(defaultImplementation_1.defaultWindowHandlerImplementation);
          }
          WindowHandlerReference2.init = function(windowHandlerInput) {
            if (WindowHandlerReference2.instance !== void 0) {
              return;
            }
            WindowHandlerReference2.instance = new WindowHandlerReference2(windowHandlerInput);
          };
          WindowHandlerReference2.getReferenceOrThrow = function() {
            if (WindowHandlerReference2.instance === void 0) {
              throw new Error("SuperTokensWindowHandler must be initialized before calling this method.");
            }
            return WindowHandlerReference2.instance;
          };
          return WindowHandlerReference2;
        })()
      );
      exports.WindowHandlerReference = WindowHandlerReference;
      exports.default = WindowHandlerReference;
    }
  });

  // node_modules/supertokens-website/utils/windowHandler/index.js
  var require_windowHandler2 = __commonJS({
    "node_modules/supertokens-website/utils/windowHandler/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      __export2(require_windowHandler());
    }
  });

  // node_modules/supertokens-web-js/lib/build/windowHandler/index.js
  var require_windowHandler3 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/windowHandler/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.WindowHandlerReference = void 0;
      var windowHandler_1 = require_windowHandler2();
      Object.defineProperty(exports, "WindowHandlerReference", {
        enumerable: true,
        get: function() {
          return windowHandler_1.WindowHandlerReference;
        }
      });
    }
  });

  // node_modules/supertokens-web-js/lib/build/constants.js
  var require_constants = __commonJS({
    "node_modules/supertokens-web-js/lib/build/constants.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.SSR_ERROR = exports.DEFAULT_API_BASE_PATH = void 0;
      exports.DEFAULT_API_BASE_PATH = "/auth";
      exports.SSR_ERROR = "\nIf you are trying to use this method doing server-side-rendering, please make sure you move this method inside a componentDidMount method or useEffect hook.";
    }
  });

  // node_modules/supertokens-web-js/lib/build/normalisedURLDomain.js
  var require_normalisedURLDomain = __commonJS({
    "node_modules/supertokens-web-js/lib/build/normalisedURLDomain.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      var NormalisedURLDomain = (
        /** @class */
        /* @__PURE__ */ (function() {
          function NormalisedURLDomain2(url) {
            var _this = this;
            this.getAsStringDangerous = function() {
              return _this.value;
            };
            this.value = normaliseURLDomainOrThrowError(url);
          }
          return NormalisedURLDomain2;
        })()
      );
      exports.default = NormalisedURLDomain;
      function normaliseURLDomainOrThrowError(input, ignoreProtocol) {
        if (ignoreProtocol === void 0) {
          ignoreProtocol = false;
        }
        function isAnIpAddress(ipaddress) {
          return /^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/.test(
            ipaddress
          );
        }
        input = input.trim();
        try {
          if (!input.startsWith("http://") && !input.startsWith("https://")) {
            throw new Error("Error converting to proper URL");
          }
          var urlObj = new URL(input);
          if (ignoreProtocol) {
            if (urlObj.hostname.startsWith("localhost") || isAnIpAddress(urlObj.hostname)) {
              input = "http://" + urlObj.host;
            } else {
              input = "https://" + urlObj.host;
            }
          } else {
            input = urlObj.protocol + "//" + urlObj.host;
          }
          return input;
        } catch (err) {
        }
        if (input.startsWith("/")) {
          throw new Error("Please provide a valid domain name");
        }
        if (input.indexOf(".") === 0) {
          input = input.substr(1);
        }
        if ((input.indexOf(".") !== -1 || input.startsWith("localhost")) && !input.startsWith("http://") && !input.startsWith("https://")) {
          input = "https://" + input;
          try {
            new URL(input);
            return normaliseURLDomainOrThrowError(input, true);
          } catch (err) {
          }
        }
        throw new Error("Please provide a valid domain name");
      }
    }
  });

  // node_modules/supertokens-web-js/lib/build/normalisedURLPath.js
  var require_normalisedURLPath = __commonJS({
    "node_modules/supertokens-web-js/lib/build/normalisedURLPath.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      var NormalisedURLPath = (
        /** @class */
        /* @__PURE__ */ (function() {
          function NormalisedURLPath2(url) {
            var _this = this;
            this.startsWith = function(other) {
              return _this.value.startsWith(other.value);
            };
            this.appendPath = function(other) {
              return new NormalisedURLPath2(_this.value + other.value);
            };
            this.getAsStringDangerous = function() {
              return _this.value;
            };
            this.value = normaliseURLPathOrThrowError(url);
          }
          return NormalisedURLPath2;
        })()
      );
      exports.default = NormalisedURLPath;
      function normaliseURLPathOrThrowError(input) {
        input = input.trim();
        try {
          if (!input.startsWith("http://") && !input.startsWith("https://")) {
            throw new Error("Error converting to proper URL");
          }
          var urlObj = new URL(input);
          input = urlObj.pathname;
          if (input.charAt(input.length - 1) === "/") {
            return input.substr(0, input.length - 1);
          }
          return input;
        } catch (err) {
        }
        if ((domainGiven(input) || input.startsWith("localhost")) && !input.startsWith("http://") && !input.startsWith("https://")) {
          input = "http://" + input;
          return normaliseURLPathOrThrowError(input);
        }
        if (input.charAt(0) !== "/") {
          input = "/" + input;
        }
        try {
          new URL("http://example.com" + input);
          return normaliseURLPathOrThrowError("http://example.com" + input);
        } catch (err) {
          throw new Error("Please provide a valid URL path");
        }
      }
      function domainGiven(input) {
        if (input.indexOf(".") === -1 || input.startsWith("/")) {
          return false;
        }
        try {
          var url = new URL(input);
          return url.hostname.indexOf(".") !== -1;
        } catch (e) {
        }
        try {
          var url = new URL("http://" + input);
          return url.hostname.indexOf(".") !== -1;
        } catch (e) {
        }
        return false;
      }
    }
  });

  // node_modules/supertokens-web-js/lib/build/types.js
  var require_types = __commonJS({
    "node_modules/supertokens-web-js/lib/build/types.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.nonPublicConfigProperties = void 0;
      exports.nonPublicConfigProperties = ["experimental"];
    }
  });

  // node_modules/supertokens-website/lib/build/normalisedURLDomain.js
  var require_normalisedURLDomain2 = __commonJS({
    "node_modules/supertokens-website/lib/build/normalisedURLDomain.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.isAnIpAddress = void 0;
      function isAnIpAddress(ipaddress) {
        return /^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/.test(
          ipaddress
        );
      }
      exports.isAnIpAddress = isAnIpAddress;
      var NormalisedURLDomain = (
        /** @class */
        /* @__PURE__ */ (function() {
          function NormalisedURLDomain2(url) {
            var _this = this;
            this.getAsStringDangerous = function() {
              return _this.value;
            };
            this.value = normaliseURLDomainOrThrowError(url);
          }
          return NormalisedURLDomain2;
        })()
      );
      exports.default = NormalisedURLDomain;
      function normaliseURLDomainOrThrowError(input, ignoreProtocol) {
        if (ignoreProtocol === void 0) {
          ignoreProtocol = false;
        }
        input = input.trim();
        try {
          if (!input.startsWith("http://") && !input.startsWith("https://")) {
            throw new Error("converting to proper URL");
          }
          var urlObj = new URL(input);
          if (ignoreProtocol) {
            if (urlObj.hostname.startsWith("localhost") || isAnIpAddress(urlObj.hostname)) {
              input = "http://" + urlObj.host;
            } else {
              input = "https://" + urlObj.host;
            }
          } else {
            input = urlObj.protocol + "//" + urlObj.host;
          }
          return input;
        } catch (err) {
        }
        if (input.startsWith("/")) {
          throw new Error("Please provide a valid domain name");
        }
        if (input.indexOf(".") === 0) {
          input = input.substr(1);
        }
        if ((input.indexOf(".") !== -1 || input.startsWith("localhost")) && !input.startsWith("http://") && !input.startsWith("https://")) {
          input = "https://" + input;
          try {
            new URL(input);
            return normaliseURLDomainOrThrowError(input, true);
          } catch (err) {
          }
        }
        throw new Error("Please provide a valid domain name");
      }
    }
  });

  // node_modules/supertokens-website/lib/build/normalisedURLPath.js
  var require_normalisedURLPath2 = __commonJS({
    "node_modules/supertokens-website/lib/build/normalisedURLPath.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      var NormalisedURLPath = (
        /** @class */
        /* @__PURE__ */ (function() {
          function NormalisedURLPath2(url) {
            var _this = this;
            this.startsWith = function(other) {
              return _this.value.startsWith(other.value);
            };
            this.appendPath = function(other) {
              return new NormalisedURLPath2(_this.value + other.value);
            };
            this.getAsStringDangerous = function() {
              return _this.value;
            };
            this.value = normaliseURLPathOrThrowError(url);
          }
          return NormalisedURLPath2;
        })()
      );
      exports.default = NormalisedURLPath;
      function normaliseURLPathOrThrowError(input) {
        input = input.trim();
        try {
          if (!input.startsWith("http://") && !input.startsWith("https://")) {
            throw new Error("converting to proper URL");
          }
          var urlObj = new URL(input);
          input = urlObj.pathname;
          if (input.charAt(input.length - 1) === "/") {
            return input.substr(0, input.length - 1);
          }
          return input;
        } catch (err) {
        }
        if ((domainGiven(input) || input.startsWith("localhost")) && !input.startsWith("http://") && !input.startsWith("https://")) {
          input = "http://" + input;
          return normaliseURLPathOrThrowError(input);
        }
        if (input.charAt(0) !== "/") {
          input = "/" + input;
        }
        try {
          new URL("http://example.com" + input);
          return normaliseURLPathOrThrowError("http://example.com" + input);
        } catch (err) {
          throw new Error("Please provide a valid URL path");
        }
      }
      function domainGiven(input) {
        if (input.indexOf(".") === -1 || input.startsWith("/")) {
          return false;
        }
        try {
          var url = new URL(input);
          return url.hostname.indexOf(".") !== -1;
        } catch (e) {
        }
        try {
          var url = new URL("http://" + input);
          return url.hostname.indexOf(".") !== -1;
        } catch (e) {
        }
        return false;
      }
    }
  });

  // node_modules/supertokens-website/lib/build/utils/index.js
  var require_utils = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/index.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.matchesDomainOrSubdomain = exports.getNormalisedUserContext = exports.validateAndNormaliseInputOrThrowError = exports.normaliseSessionScopeOrThrowError = exports.normaliseURLPathOrThrowError = exports.normaliseURLDomainOrThrowError = void 0;
      var normalisedURLDomain_1 = require_normalisedURLDomain2();
      var normalisedURLPath_1 = require_normalisedURLPath2();
      var windowHandler_1 = require_windowHandler();
      function normaliseURLDomainOrThrowError(input) {
        var str = new normalisedURLDomain_1.default(input).getAsStringDangerous();
        return str;
      }
      exports.normaliseURLDomainOrThrowError = normaliseURLDomainOrThrowError;
      function normaliseURLPathOrThrowError(input) {
        return new normalisedURLPath_1.default(input).getAsStringDangerous();
      }
      exports.normaliseURLPathOrThrowError = normaliseURLPathOrThrowError;
      function normaliseSessionScopeOrThrowError(sessionScope) {
        function helper(sessionScope2) {
          sessionScope2 = sessionScope2.trim().toLowerCase();
          if (sessionScope2.startsWith(".")) {
            sessionScope2 = sessionScope2.substr(1);
          }
          if (!sessionScope2.startsWith("http://") && !sessionScope2.startsWith("https://")) {
            sessionScope2 = "http://" + sessionScope2;
          }
          try {
            var urlObj = new URL(sessionScope2);
            sessionScope2 = urlObj.hostname;
            return sessionScope2;
          } catch (err) {
            throw new Error("Please provide a valid sessionScope");
          }
        }
        var noDotNormalised = helper(sessionScope);
        if (noDotNormalised === "localhost" || (0, normalisedURLDomain_1.isAnIpAddress)(noDotNormalised)) {
          return noDotNormalised;
        }
        if (sessionScope.startsWith(".")) {
          return "." + noDotNormalised;
        }
        return noDotNormalised;
      }
      exports.normaliseSessionScopeOrThrowError = normaliseSessionScopeOrThrowError;
      function validateAndNormaliseInputOrThrowError(options) {
        var _this = this;
        var apiDomain = normaliseURLDomainOrThrowError(options.apiDomain);
        var apiBasePath = normaliseURLPathOrThrowError("/auth");
        if (options.apiBasePath !== void 0) {
          apiBasePath = normaliseURLPathOrThrowError(options.apiBasePath);
        }
        var defaultSessionScope = windowHandler_1.default.getReferenceOrThrow().windowHandler.location.getHostName();
        var sessionTokenFrontendDomain = normaliseSessionScopeOrThrowError(
          options !== void 0 && options.sessionTokenFrontendDomain !== void 0 ? options.sessionTokenFrontendDomain : defaultSessionScope
        );
        var sessionExpiredStatusCode = 401;
        if (options.sessionExpiredStatusCode !== void 0) {
          sessionExpiredStatusCode = options.sessionExpiredStatusCode;
        }
        var invalidClaimStatusCode = 403;
        if (options.invalidClaimStatusCode !== void 0) {
          invalidClaimStatusCode = options.invalidClaimStatusCode;
        }
        if (sessionExpiredStatusCode === invalidClaimStatusCode) {
          throw new Error("sessionExpiredStatusCode and invalidClaimStatusCode cannot be the same.");
        }
        var autoAddCredentials = true;
        if (options.autoAddCredentials !== void 0) {
          autoAddCredentials = options.autoAddCredentials;
        }
        var isInIframe = false;
        if (options.isInIframe !== void 0) {
          isInIframe = options.isInIframe;
        }
        var sessionTokenBackendDomain = void 0;
        if (options.sessionTokenBackendDomain !== void 0) {
          sessionTokenBackendDomain = normaliseSessionScopeOrThrowError(options.sessionTokenBackendDomain);
        }
        var maxRetryAttemptsForSessionRefresh = 10;
        if (options.maxRetryAttemptsForSessionRefresh !== void 0) {
          if (options.maxRetryAttemptsForSessionRefresh < 0) {
            throw new Error("maxRetryAttemptsForSessionRefresh must be greater than or equal to 0.");
          }
          maxRetryAttemptsForSessionRefresh = options.maxRetryAttemptsForSessionRefresh;
        }
        var preAPIHook = function(context) {
          return __awaiter(_this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, { url: context.url, requestInit: context.requestInit }];
            });
          });
        };
        if (options.preAPIHook !== void 0) {
          preAPIHook = options.preAPIHook;
        }
        var postAPIHook = function() {
          return __awaiter(_this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [
                2
                /*return*/
              ];
            });
          });
        };
        if (options.postAPIHook !== void 0) {
          postAPIHook = options.postAPIHook;
        }
        var onHandleEvent = function() {
        };
        if (options.onHandleEvent !== void 0) {
          onHandleEvent = options.onHandleEvent;
        }
        var override = __assign(
          {
            functions: function(oI) {
              return oI;
            }
          },
          options.override
        );
        return {
          apiDomain,
          apiBasePath,
          sessionTokenFrontendDomain,
          sessionExpiredStatusCode,
          invalidClaimStatusCode,
          autoAddCredentials,
          isInIframe,
          tokenTransferMethod: options.tokenTransferMethod !== void 0 ? options.tokenTransferMethod : "cookie",
          sessionTokenBackendDomain,
          maxRetryAttemptsForSessionRefresh,
          preAPIHook,
          postAPIHook,
          onHandleEvent,
          override
        };
      }
      exports.validateAndNormaliseInputOrThrowError = validateAndNormaliseInputOrThrowError;
      function getNormalisedUserContext(userContext) {
        if (userContext === void 0) {
          return {};
        }
        return userContext;
      }
      exports.getNormalisedUserContext = getNormalisedUserContext;
      function matchesDomainOrSubdomain(hostname, str) {
        var parts = hostname.split(".");
        for (var i = 0; i < parts.length; i++) {
          var subdomainCandidate = parts.slice(i).join(".");
          if (subdomainCandidate === str || ".".concat(subdomainCandidate) === str) {
            return true;
          }
        }
        return false;
      }
      exports.matchesDomainOrSubdomain = matchesDomainOrSubdomain;
    }
  });

  // node_modules/supertokens-website/lib/build/processState.js
  var require_processState = __commonJS({
    "node_modules/supertokens-website/lib/build/processState.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.ProcessState = exports.PROCESS_STATE = void 0;
      var PROCESS_STATE;
      (function(PROCESS_STATE2) {
        PROCESS_STATE2[PROCESS_STATE2["CALLING_INTERCEPTION_REQUEST"] = 0] = "CALLING_INTERCEPTION_REQUEST";
        PROCESS_STATE2[PROCESS_STATE2["CALLING_INTERCEPTION_RESPONSE"] = 1] = "CALLING_INTERCEPTION_RESPONSE";
      })(PROCESS_STATE = exports.PROCESS_STATE || (exports.PROCESS_STATE = {}));
      var ProcessState = (
        /** @class */
        (function() {
          function ProcessState2() {
            var _this = this;
            this.history = [];
            this.addState = function(state) {
              try {
                if (process !== void 0 && process.env !== void 0 && process.env.TEST_MODE === "testing") {
                  _this.history.push(state);
                }
              } catch (ignored) {
              }
            };
            this.getEventByLastEventByName = function(state) {
              for (var i = _this.history.length - 1; i >= 0; i--) {
                if (_this.history[i] == state) {
                  return _this.history[i];
                }
              }
              return void 0;
            };
            this.reset = function() {
              _this.history = [];
            };
            this.waitForEvent = function(state, timeInMS) {
              if (timeInMS === void 0) {
                timeInMS = 7e3;
              }
              return __awaiter(_this, void 0, void 0, function() {
                var startTime;
                var _this2 = this;
                return __generator(this, function(_a) {
                  startTime = Date.now();
                  return [
                    2,
                    new Promise(function(resolve2) {
                      var actualThis = _this2;
                      function tryAndGet() {
                        var result = actualThis.getEventByLastEventByName(state);
                        if (result === void 0) {
                          if (Date.now() - startTime > timeInMS) {
                            resolve2(void 0);
                          } else {
                            setTimeout(tryAndGet, 1e3);
                          }
                        } else {
                          resolve2(result);
                        }
                      }
                      tryAndGet();
                    })
                  ];
                });
              });
            };
          }
          ProcessState2.getInstance = function() {
            if (ProcessState2.instance == void 0) {
              ProcessState2.instance = new ProcessState2();
            }
            return ProcessState2.instance;
          };
          return ProcessState2;
        })()
      );
      exports.ProcessState = ProcessState;
    }
  });

  // node_modules/supertokens-website/lib/build/version.js
  var require_version = __commonJS({
    "node_modules/supertokens-website/lib/build/version.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.supported_fdi = exports.package_version = void 0;
      exports.package_version = "20.1.6";
      exports.supported_fdi = ["1.16", "1.17", "1.18", "1.19", "2.0", "3.0", "3.1", "4.0", "4.1"];
    }
  });

  // node_modules/supertokens-website/lib/build/utils/cookieHandler/defaultImplementation.js
  var require_defaultImplementation2 = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/cookieHandler/defaultImplementation.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.defaultCookieHandlerImplementation = void 0;
      var windowHandler_1 = require_windowHandler();
      exports.defaultCookieHandlerImplementation = {
        getCookie: function() {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [
                2,
                windowHandler_1.default.getReferenceOrThrow().windowHandler.getWindowUnsafe().document.cookie
              ];
            });
          });
        },
        setCookie: function(cookieString) {
          return __awaiter(this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              windowHandler_1.default.getReferenceOrThrow().windowHandler.getWindowUnsafe().document.cookie = cookieString;
              return [
                2
                /*return*/
              ];
            });
          });
        }
      };
    }
  });

  // node_modules/supertokens-website/lib/build/utils/cookieHandler/index.js
  var require_cookieHandler = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/cookieHandler/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.CookieHandlerReference = void 0;
      var defaultImplementation_1 = require_defaultImplementation2();
      var CookieHandlerReference = (
        /** @class */
        (function() {
          function CookieHandlerReference2(cookieHandlerInput) {
            var cookieHandlerFunc = function(original) {
              return original;
            };
            if (cookieHandlerInput !== void 0) {
              cookieHandlerFunc = cookieHandlerInput;
            }
            this.cookieHandler = cookieHandlerFunc(defaultImplementation_1.defaultCookieHandlerImplementation);
          }
          CookieHandlerReference2.init = function(cookieHandlerInput) {
            if (CookieHandlerReference2.instance !== void 0) {
              return;
            }
            CookieHandlerReference2.instance = new CookieHandlerReference2(cookieHandlerInput);
          };
          CookieHandlerReference2.getReferenceOrThrow = function() {
            if (CookieHandlerReference2.instance === void 0) {
              throw new Error("SuperTokensCookieHandler must be initialized before calling this method.");
            }
            return CookieHandlerReference2.instance;
          };
          return CookieHandlerReference2;
        })()
      );
      exports.CookieHandlerReference = CookieHandlerReference;
      exports.default = CookieHandlerReference;
    }
  });

  // node_modules/browser-tabs-lock/processLock.js
  var require_processLock = __commonJS({
    "node_modules/browser-tabs-lock/processLock.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      var ProcessLocking = (
        /** @class */
        (function() {
          function ProcessLocking2() {
            var _this = this;
            this.locked = /* @__PURE__ */ new Map();
            this.addToLocked = function(key, toAdd) {
              var callbacks = _this.locked.get(key);
              if (callbacks === void 0) {
                if (toAdd === void 0) {
                  _this.locked.set(key, []);
                } else {
                  _this.locked.set(key, [toAdd]);
                }
              } else {
                if (toAdd !== void 0) {
                  callbacks.unshift(toAdd);
                  _this.locked.set(key, callbacks);
                }
              }
            };
            this.isLocked = function(key) {
              return _this.locked.has(key);
            };
            this.lock = function(key) {
              return new Promise(function(resolve2, reject) {
                if (_this.isLocked(key)) {
                  _this.addToLocked(key, resolve2);
                } else {
                  _this.addToLocked(key);
                  resolve2();
                }
              });
            };
            this.unlock = function(key) {
              var callbacks = _this.locked.get(key);
              if (callbacks === void 0 || callbacks.length === 0) {
                _this.locked.delete(key);
                return;
              }
              var toCall = callbacks.pop();
              _this.locked.set(key, callbacks);
              if (toCall !== void 0) {
                setTimeout(toCall, 0);
              }
            };
          }
          ProcessLocking2.getInstance = function() {
            if (ProcessLocking2.instance === void 0) {
              ProcessLocking2.instance = new ProcessLocking2();
            }
            return ProcessLocking2.instance;
          };
          return ProcessLocking2;
        })()
      );
      function getLock() {
        return ProcessLocking.getInstance();
      }
      exports.default = getLock;
    }
  });

  // node_modules/browser-tabs-lock/index.js
  var require_browser_tabs_lock = __commonJS({
    "node_modules/browser-tabs-lock/index.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : new P(function(resolve3) {
              resolve3(result.value);
            }).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = { label: 0, sent: function() {
          if (t[0] & 1) throw t[1];
          return t[1];
        }, trys: [], ops: [] }, f, y, t, g;
        return g = { next: verb(0), "throw": verb(1), "return": verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_) try {
            if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done) return t;
            if (y = 0, t) op = [op[0] & 2, t.value];
            switch (op[0]) {
              case 0:
              case 1:
                t = op;
                break;
              case 4:
                _.label++;
                return { value: op[1], done: false };
              case 5:
                _.label++;
                y = op[1];
                op = [0];
                continue;
              case 7:
                op = _.ops.pop();
                _.trys.pop();
                continue;
              default:
                if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                  _ = 0;
                  continue;
                }
                if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                  _.label = op[1];
                  break;
                }
                if (op[0] === 6 && _.label < t[1]) {
                  _.label = t[1];
                  t = op;
                  break;
                }
                if (t && _.label < t[2]) {
                  _.label = t[2];
                  _.ops.push(op);
                  break;
                }
                if (t[2]) _.ops.pop();
                _.trys.pop();
                continue;
            }
            op = body.call(thisArg, _);
          } catch (e) {
            op = [6, e];
            y = 0;
          } finally {
            f = t = 0;
          }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      var _this = exports;
      Object.defineProperty(exports, "__esModule", { value: true });
      var processLock_1 = require_processLock();
      var LOCK_STORAGE_KEY = "browser-tabs-lock-key";
      var DEFAULT_STORAGE_HANDLER = {
        key: function(index) {
          return __awaiter(_this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              throw new Error("Unsupported");
            });
          });
        },
        getItem: function(key) {
          return __awaiter(_this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              throw new Error("Unsupported");
            });
          });
        },
        clear: function() {
          return __awaiter(_this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              return [2, window.localStorage.clear()];
            });
          });
        },
        removeItem: function(key) {
          return __awaiter(_this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              throw new Error("Unsupported");
            });
          });
        },
        setItem: function(key, value) {
          return __awaiter(_this, void 0, void 0, function() {
            return __generator(this, function(_a) {
              throw new Error("Unsupported");
            });
          });
        },
        keySync: function(index) {
          return window.localStorage.key(index);
        },
        getItemSync: function(key) {
          return window.localStorage.getItem(key);
        },
        clearSync: function() {
          return window.localStorage.clear();
        },
        removeItemSync: function(key) {
          return window.localStorage.removeItem(key);
        },
        setItemSync: function(key, value) {
          return window.localStorage.setItem(key, value);
        }
      };
      function delay(milliseconds) {
        return new Promise(function(resolve2) {
          return setTimeout(resolve2, milliseconds);
        });
      }
      function generateRandomString(length) {
        var CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXTZabcdefghiklmnopqrstuvwxyz";
        var randomstring = "";
        for (var i = 0; i < length; i++) {
          var INDEX = Math.floor(Math.random() * CHARS.length);
          randomstring += CHARS[INDEX];
        }
        return randomstring;
      }
      function getLockId() {
        return Date.now().toString() + generateRandomString(15);
      }
      var SuperTokensLock = (
        /** @class */
        (function() {
          function SuperTokensLock2(storageHandler) {
            this.acquiredIatSet = /* @__PURE__ */ new Set();
            this.storageHandler = void 0;
            this.id = getLockId();
            this.acquireLock = this.acquireLock.bind(this);
            this.releaseLock = this.releaseLock.bind(this);
            this.releaseLock__private__ = this.releaseLock__private__.bind(this);
            this.waitForSomethingToChange = this.waitForSomethingToChange.bind(this);
            this.refreshLockWhileAcquired = this.refreshLockWhileAcquired.bind(this);
            this.storageHandler = storageHandler;
            if (SuperTokensLock2.waiters === void 0) {
              SuperTokensLock2.waiters = [];
            }
          }
          SuperTokensLock2.prototype.acquireLock = function(lockKey, timeout) {
            if (timeout === void 0) {
              timeout = 5e3;
            }
            return __awaiter(this, void 0, void 0, function() {
              var iat, MAX_TIME, STORAGE_KEY, STORAGE, lockObj, TIMEOUT_KEY, lockObjPostDelay, parsedLockObjPostDelay;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    iat = Date.now() + generateRandomString(4);
                    MAX_TIME = Date.now() + timeout;
                    STORAGE_KEY = LOCK_STORAGE_KEY + "-" + lockKey;
                    STORAGE = this.storageHandler === void 0 ? DEFAULT_STORAGE_HANDLER : this.storageHandler;
                    _a.label = 1;
                  case 1:
                    if (!(Date.now() < MAX_TIME)) return [3, 8];
                    return [4, delay(30)];
                  case 2:
                    _a.sent();
                    lockObj = STORAGE.getItemSync(STORAGE_KEY);
                    if (!(lockObj === null)) return [3, 5];
                    TIMEOUT_KEY = this.id + "-" + lockKey + "-" + iat;
                    return [4, delay(Math.floor(Math.random() * 25))];
                  case 3:
                    _a.sent();
                    STORAGE.setItemSync(STORAGE_KEY, JSON.stringify({
                      id: this.id,
                      iat,
                      timeoutKey: TIMEOUT_KEY,
                      timeAcquired: Date.now(),
                      timeRefreshed: Date.now()
                    }));
                    return [4, delay(30)];
                  case 4:
                    _a.sent();
                    lockObjPostDelay = STORAGE.getItemSync(STORAGE_KEY);
                    if (lockObjPostDelay !== null) {
                      parsedLockObjPostDelay = JSON.parse(lockObjPostDelay);
                      if (parsedLockObjPostDelay.id === this.id && parsedLockObjPostDelay.iat === iat) {
                        this.acquiredIatSet.add(iat);
                        this.refreshLockWhileAcquired(STORAGE_KEY, iat);
                        return [2, true];
                      }
                    }
                    return [3, 7];
                  case 5:
                    SuperTokensLock2.lockCorrector(this.storageHandler === void 0 ? DEFAULT_STORAGE_HANDLER : this.storageHandler);
                    return [4, this.waitForSomethingToChange(MAX_TIME)];
                  case 6:
                    _a.sent();
                    _a.label = 7;
                  case 7:
                    iat = Date.now() + generateRandomString(4);
                    return [3, 1];
                  case 8:
                    return [2, false];
                }
              });
            });
          };
          SuperTokensLock2.prototype.refreshLockWhileAcquired = function(storageKey, iat) {
            return __awaiter(this, void 0, void 0, function() {
              var _this2 = this;
              return __generator(this, function(_a) {
                setTimeout(function() {
                  return __awaiter(_this2, void 0, void 0, function() {
                    var STORAGE, lockObj, parsedLockObj;
                    return __generator(this, function(_a2) {
                      switch (_a2.label) {
                        case 0:
                          return [4, processLock_1.default().lock(iat)];
                        case 1:
                          _a2.sent();
                          if (!this.acquiredIatSet.has(iat)) {
                            processLock_1.default().unlock(iat);
                            return [
                              2
                              /*return*/
                            ];
                          }
                          STORAGE = this.storageHandler === void 0 ? DEFAULT_STORAGE_HANDLER : this.storageHandler;
                          lockObj = STORAGE.getItemSync(storageKey);
                          if (lockObj !== null) {
                            parsedLockObj = JSON.parse(lockObj);
                            parsedLockObj.timeRefreshed = Date.now();
                            STORAGE.setItemSync(storageKey, JSON.stringify(parsedLockObj));
                            processLock_1.default().unlock(iat);
                          } else {
                            processLock_1.default().unlock(iat);
                            return [
                              2
                              /*return*/
                            ];
                          }
                          this.refreshLockWhileAcquired(storageKey, iat);
                          return [
                            2
                            /*return*/
                          ];
                      }
                    });
                  });
                }, 1e3);
                return [
                  2
                  /*return*/
                ];
              });
            });
          };
          SuperTokensLock2.prototype.waitForSomethingToChange = function(MAX_TIME) {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    return [4, new Promise(function(resolve2) {
                      var resolvedCalled = false;
                      var startedAt = Date.now();
                      var MIN_TIME_TO_WAIT = 50;
                      var removedListeners = false;
                      function stopWaiting() {
                        if (!removedListeners) {
                          window.removeEventListener("storage", stopWaiting);
                          SuperTokensLock2.removeFromWaiting(stopWaiting);
                          clearTimeout(timeOutId);
                          removedListeners = true;
                        }
                        if (!resolvedCalled) {
                          resolvedCalled = true;
                          var timeToWait = MIN_TIME_TO_WAIT - (Date.now() - startedAt);
                          if (timeToWait > 0) {
                            setTimeout(resolve2, timeToWait);
                          } else {
                            resolve2(null);
                          }
                        }
                      }
                      window.addEventListener("storage", stopWaiting);
                      SuperTokensLock2.addToWaiting(stopWaiting);
                      var timeOutId = setTimeout(stopWaiting, Math.max(0, MAX_TIME - Date.now()));
                    })];
                  case 1:
                    _a.sent();
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          };
          SuperTokensLock2.addToWaiting = function(func) {
            this.removeFromWaiting(func);
            if (SuperTokensLock2.waiters === void 0) {
              return;
            }
            SuperTokensLock2.waiters.push(func);
          };
          SuperTokensLock2.removeFromWaiting = function(func) {
            if (SuperTokensLock2.waiters === void 0) {
              return;
            }
            SuperTokensLock2.waiters = SuperTokensLock2.waiters.filter(function(i) {
              return i !== func;
            });
          };
          SuperTokensLock2.notifyWaiters = function() {
            if (SuperTokensLock2.waiters === void 0) {
              return;
            }
            var waiters = SuperTokensLock2.waiters.slice();
            waiters.forEach(function(i) {
              return i();
            });
          };
          SuperTokensLock2.prototype.releaseLock = function(lockKey) {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    return [4, this.releaseLock__private__(lockKey)];
                  case 1:
                    return [2, _a.sent()];
                }
              });
            });
          };
          SuperTokensLock2.prototype.releaseLock__private__ = function(lockKey) {
            return __awaiter(this, void 0, void 0, function() {
              var STORAGE, STORAGE_KEY, lockObj, parsedlockObj;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    STORAGE = this.storageHandler === void 0 ? DEFAULT_STORAGE_HANDLER : this.storageHandler;
                    STORAGE_KEY = LOCK_STORAGE_KEY + "-" + lockKey;
                    lockObj = STORAGE.getItemSync(STORAGE_KEY);
                    if (lockObj === null) {
                      return [
                        2
                        /*return*/
                      ];
                    }
                    parsedlockObj = JSON.parse(lockObj);
                    if (!(parsedlockObj.id === this.id)) return [3, 2];
                    return [4, processLock_1.default().lock(parsedlockObj.iat)];
                  case 1:
                    _a.sent();
                    this.acquiredIatSet.delete(parsedlockObj.iat);
                    STORAGE.removeItemSync(STORAGE_KEY);
                    processLock_1.default().unlock(parsedlockObj.iat);
                    SuperTokensLock2.notifyWaiters();
                    _a.label = 2;
                  case 2:
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          };
          SuperTokensLock2.lockCorrector = function(storageHandler) {
            var MIN_ALLOWED_TIME = Date.now() - 5e3;
            var STORAGE = storageHandler;
            var KEYS = [];
            var currIndex = 0;
            while (true) {
              var key = STORAGE.keySync(currIndex);
              if (key === null) {
                break;
              }
              KEYS.push(key);
              currIndex++;
            }
            var notifyWaiters = false;
            for (var i = 0; i < KEYS.length; i++) {
              var LOCK_KEY = KEYS[i];
              if (LOCK_KEY.includes(LOCK_STORAGE_KEY)) {
                var lockObj = STORAGE.getItemSync(LOCK_KEY);
                if (lockObj !== null) {
                  var parsedlockObj = JSON.parse(lockObj);
                  if (parsedlockObj.timeRefreshed === void 0 && parsedlockObj.timeAcquired < MIN_ALLOWED_TIME || parsedlockObj.timeRefreshed !== void 0 && parsedlockObj.timeRefreshed < MIN_ALLOWED_TIME) {
                    STORAGE.removeItemSync(LOCK_KEY);
                    notifyWaiters = true;
                  }
                }
              }
            }
            if (notifyWaiters) {
              SuperTokensLock2.notifyWaiters();
            }
          };
          SuperTokensLock2.waiters = void 0;
          return SuperTokensLock2;
        })()
      );
      exports.default = SuperTokensLock;
    }
  });

  // node_modules/supertokens-website/lib/build/utils/lockFactory/index.js
  var require_lockFactory = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/lockFactory/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.LockFactoryReference = void 0;
      var browser_tabs_lock_1 = require_browser_tabs_lock();
      var defaultFactory = function(storageHandler) {
        return function() {
          return Promise.resolve(new browser_tabs_lock_1.default(storageHandler));
        };
      };
      var LockFactoryReference = (
        /** @class */
        (function() {
          function LockFactoryReference2(lockFactory) {
            this.lockFactory = lockFactory;
          }
          LockFactoryReference2.init = function(lockFactory, storageHandler) {
            if (this.instance !== void 0) {
              return;
            }
            this.instance = new LockFactoryReference2(
              lockFactory !== null && lockFactory !== void 0 ? lockFactory : defaultFactory(storageHandler)
            );
          };
          LockFactoryReference2.getReferenceOrThrow = function() {
            if (LockFactoryReference2.instance === void 0) {
              throw new Error("SuperTokensLockReference must be initialized before calling this method.");
            }
            return LockFactoryReference2.instance;
          };
          return LockFactoryReference2;
        })()
      );
      exports.LockFactoryReference = LockFactoryReference;
      exports.default = LockFactoryReference;
    }
  });

  // node_modules/supertokens-website/lib/build/logger.js
  var require_logger = __commonJS({
    "node_modules/supertokens-website/lib/build/logger.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.logDebugMessage = exports.disableLogging = exports.enableLogging = void 0;
      var version_1 = require_version();
      var SUPERTOKENS_DEBUG_NAMESPACE = "com.supertokens";
      var __supertokensWebsiteLogging = false;
      function enableLogging() {
        __supertokensWebsiteLogging = true;
      }
      exports.enableLogging = enableLogging;
      function disableLogging() {
        __supertokensWebsiteLogging = false;
      }
      exports.disableLogging = disableLogging;
      function logDebugMessage(message) {
        if (__supertokensWebsiteLogging) {
          console.log(
            "".concat(SUPERTOKENS_DEBUG_NAMESPACE, ' {t: "').concat((/* @__PURE__ */ new Date()).toISOString(), '", message: "').concat(message, '", supertokens-website-ver: "').concat(version_1.package_version, '"}')
          );
        }
      }
      exports.logDebugMessage = logDebugMessage;
    }
  });

  // node_modules/supertokens-website/lib/build/utils/dateProvider/defaultImplementation.js
  var require_defaultImplementation3 = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/dateProvider/defaultImplementation.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.DateProvider = void 0;
      var windowHandler_1 = require_windowHandler();
      var DateProvider = (
        /** @class */
        (function() {
          function DateProvider2() {
            this.clockSkewInMillis = 0;
            this.thresholdInSeconds = 7;
          }
          DateProvider2.init = function() {
            if (DateProvider2.instance !== void 0) {
              return;
            }
            DateProvider2.instance = new DateProvider2();
            var localStorage2 = windowHandler_1.default.getReferenceOrThrow().windowHandler.localStorage;
            var stored = localStorage2.getItemSync(DateProvider2.CLOCK_SKEW_KEY);
            var clockSkewInMillis = stored !== null ? parseInt(stored, 10) : 0;
            DateProvider2.instance.setClientClockSkewInMillis(clockSkewInMillis);
          };
          DateProvider2.getReferenceOrThrow = function() {
            if (DateProvider2.instance === void 0) {
              throw new Error("DateProvider must be initialized before calling this method.");
            }
            return DateProvider2.instance;
          };
          DateProvider2.prototype.getThresholdInSeconds = function() {
            return this.thresholdInSeconds;
          };
          DateProvider2.prototype.setThresholdInSeconds = function(thresholdInSeconds) {
            this.thresholdInSeconds = thresholdInSeconds;
          };
          DateProvider2.prototype.setClientClockSkewInMillis = function(clockSkewInMillis) {
            this.clockSkewInMillis = Math.abs(clockSkewInMillis) >= this.thresholdInSeconds * 1e3 ? clockSkewInMillis : 0;
            var localStorage2 = windowHandler_1.default.getReferenceOrThrow().windowHandler.localStorage;
            localStorage2.setItemSync(DateProvider2.CLOCK_SKEW_KEY, String(clockSkewInMillis));
          };
          DateProvider2.prototype.getClientClockSkewInMillis = function() {
            return this.clockSkewInMillis;
          };
          DateProvider2.prototype.now = function() {
            return Date.now() + this.getClientClockSkewInMillis();
          };
          DateProvider2.CLOCK_SKEW_KEY = "__st_clockSkewInMillis";
          return DateProvider2;
        })()
      );
      exports.DateProvider = DateProvider;
    }
  });

  // node_modules/supertokens-website/lib/build/utils/dateProvider/index.js
  var require_dateProvider = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/dateProvider/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.DateProviderReference = void 0;
      var defaultImplementation_1 = require_defaultImplementation3();
      var DateProviderReference = (
        /** @class */
        (function() {
          function DateProviderReference2(dateProviderInput) {
            if (dateProviderInput !== void 0) {
              this.dateProvider = dateProviderInput();
            } else {
              defaultImplementation_1.DateProvider.init();
              this.dateProvider = defaultImplementation_1.DateProvider.getReferenceOrThrow();
            }
          }
          DateProviderReference2.init = function(dateProviderInput) {
            if (DateProviderReference2.instance !== void 0) {
              return;
            }
            DateProviderReference2.instance = new DateProviderReference2(dateProviderInput);
          };
          DateProviderReference2.getReferenceOrThrow = function() {
            if (DateProviderReference2.instance === void 0) {
              throw new Error("SuperTokensDateProvider must be initialized before calling this method.");
            }
            return DateProviderReference2.instance;
          };
          return DateProviderReference2;
        })()
      );
      exports.DateProviderReference = DateProviderReference;
      exports.default = DateProviderReference;
    }
  });

  // node_modules/supertokens-website/lib/build/fetch.js
  var require_fetch = __commonJS({
    "node_modules/supertokens-website/lib/build/fetch.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.updateClockSkewUsingFrontToken = exports.fireSessionUpdateEventsIfNecessary = exports.setFrontToken = exports.getFrontToken = exports.setAntiCSRF = exports.saveLastAccessTokenUpdate = exports.getTokenForHeaderAuth = exports.setToken = exports.getStorageNameForToken = exports.getLocalSessionState = exports.onInvalidClaimResponse = exports.onTokenUpdate = exports.onUnauthorisedResponse = exports.FrontToken = exports.AntiCsrfToken = void 0;
      var processState_1 = require_processState();
      var version_1 = require_version();
      var cookieHandler_1 = require_cookieHandler();
      var windowHandler_1 = require_windowHandler();
      var lockFactory_1 = require_lockFactory();
      var logger_1 = require_logger();
      var dateProvider_1 = require_dateProvider();
      var AntiCsrfToken = (
        /** @class */
        (function() {
          function AntiCsrfToken2() {
          }
          AntiCsrfToken2.getToken = function(associatedAccessTokenUpdate) {
            return __awaiter(this, void 0, void 0, function() {
              var antiCsrf;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("AntiCsrfToken.getToken: called");
                    if (associatedAccessTokenUpdate === void 0) {
                      AntiCsrfToken2.tokenInfo = void 0;
                      (0, logger_1.logDebugMessage)("AntiCsrfToken.getToken: returning undefined");
                      return [2, void 0];
                    }
                    if (!(AntiCsrfToken2.tokenInfo === void 0)) return [3, 2];
                    return [4, getAntiCSRFToken()];
                  case 1:
                    antiCsrf = _a.sent();
                    if (antiCsrf === null) {
                      (0, logger_1.logDebugMessage)("AntiCsrfToken.getToken: returning undefined");
                      return [2, void 0];
                    }
                    AntiCsrfToken2.tokenInfo = {
                      antiCsrf,
                      associatedAccessTokenUpdate
                    };
                    return [3, 4];
                  case 2:
                    if (!(AntiCsrfToken2.tokenInfo.associatedAccessTokenUpdate !== associatedAccessTokenUpdate))
                      return [3, 4];
                    AntiCsrfToken2.tokenInfo = void 0;
                    return [4, AntiCsrfToken2.getToken(associatedAccessTokenUpdate)];
                  case 3:
                    return [2, _a.sent()];
                  case 4:
                    (0, logger_1.logDebugMessage)("AntiCsrfToken.getToken: returning: " + AntiCsrfToken2.tokenInfo.antiCsrf);
                    return [2, AntiCsrfToken2.tokenInfo.antiCsrf];
                }
              });
            });
          };
          AntiCsrfToken2.removeToken = function() {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("AntiCsrfToken.removeToken: called");
                    AntiCsrfToken2.tokenInfo = void 0;
                    return [4, setAntiCSRF(void 0)];
                  case 1:
                    _a.sent();
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          };
          AntiCsrfToken2.setItem = function(associatedAccessTokenUpdate, antiCsrf) {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    if (associatedAccessTokenUpdate === void 0) {
                      AntiCsrfToken2.tokenInfo = void 0;
                      return [
                        2
                        /*return*/
                      ];
                    }
                    (0, logger_1.logDebugMessage)("AntiCsrfToken.setItem: called");
                    return [4, setAntiCSRF(antiCsrf)];
                  case 1:
                    _a.sent();
                    AntiCsrfToken2.tokenInfo = {
                      antiCsrf,
                      associatedAccessTokenUpdate
                    };
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          };
          return AntiCsrfToken2;
        })()
      );
      exports.AntiCsrfToken = AntiCsrfToken;
      var FrontToken = (
        /** @class */
        (function() {
          function FrontToken2() {
          }
          FrontToken2.getTokenInfo = function() {
            return __awaiter(this, void 0, void 0, function() {
              var frontToken, response;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("FrontToken.getTokenInfo: called");
                    return [4, getFrontToken()];
                  case 1:
                    frontToken = _a.sent();
                    if (!(frontToken === null)) return [3, 5];
                    return [4, getLocalSessionState(false)];
                  case 2:
                    if (!(_a.sent().status === "EXISTS")) return [3, 4];
                    return [
                      4,
                      new Promise(function(resolve2) {
                        FrontToken2.waiters.push(resolve2);
                      })
                    ];
                  case 3:
                    _a.sent();
                    return [2, FrontToken2.getTokenInfo()];
                  case 4:
                    return [2, void 0];
                  case 5:
                    response = parseFrontToken(frontToken);
                    (0, logger_1.logDebugMessage)("FrontToken.getTokenInfo: returning ate: " + response.ate);
                    (0, logger_1.logDebugMessage)("FrontToken.getTokenInfo: returning uid: " + response.uid);
                    (0, logger_1.logDebugMessage)("FrontToken.getTokenInfo: returning up: " + response.up);
                    return [2, response];
                }
              });
            });
          };
          FrontToken2.removeToken = function() {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("FrontToken.removeToken: called");
                    return [4, setFrontToken(void 0)];
                  case 1:
                    _a.sent();
                    return [4, setToken("access", "")];
                  case 2:
                    _a.sent();
                    return [4, setToken("refresh", "")];
                  case 3:
                    _a.sent();
                    return [4, AntiCsrfToken.removeToken()];
                  case 4:
                    _a.sent();
                    FrontToken2.waiters.forEach(function(f) {
                      return f(void 0);
                    });
                    FrontToken2.waiters = [];
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          };
          FrontToken2.setItem = function(frontToken) {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    return [4, saveLastAccessTokenUpdate()];
                  case 1:
                    _a.sent();
                    if (frontToken === "remove") {
                      return [2, FrontToken2.removeToken()];
                    }
                    (0, logger_1.logDebugMessage)("FrontToken.setItem: called");
                    return [4, setFrontToken(frontToken)];
                  case 2:
                    _a.sent();
                    FrontToken2.waiters.forEach(function(f) {
                      return f(void 0);
                    });
                    FrontToken2.waiters = [];
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          };
          FrontToken2.doesTokenExists = function() {
            return __awaiter(this, void 0, void 0, function() {
              var frontToken;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    return [4, getFrontTokenFromCookie()];
                  case 1:
                    frontToken = _a.sent();
                    return [2, frontToken !== null];
                }
              });
            });
          };
          FrontToken2.waiters = [];
          return FrontToken2;
        })()
      );
      exports.FrontToken = FrontToken;
      var AuthHttpRequest = (
        /** @class */
        (function() {
          function AuthHttpRequest2() {
          }
          AuthHttpRequest2.init = function(config, recipeImpl) {
            (0, logger_1.logDebugMessage)("init: called");
            (0, logger_1.logDebugMessage)("init: Input apiBasePath: " + config.apiBasePath);
            (0, logger_1.logDebugMessage)("init: Input apiDomain: " + config.apiDomain);
            (0, logger_1.logDebugMessage)("init: Input autoAddCredentials: " + config.autoAddCredentials);
            (0, logger_1.logDebugMessage)("init: Input sessionTokenBackendDomain: " + config.sessionTokenBackendDomain);
            (0, logger_1.logDebugMessage)("init: Input isInIframe: " + config.isInIframe);
            (0, logger_1.logDebugMessage)("init: Input sessionExpiredStatusCode: " + config.sessionExpiredStatusCode);
            (0, logger_1.logDebugMessage)("init: Input sessionTokenFrontendDomain: " + config.sessionTokenFrontendDomain);
            (0, logger_1.logDebugMessage)("init: Input tokenTransferMethod: " + config.tokenTransferMethod);
            var fetchedWindow = windowHandler_1.default.getReferenceOrThrow().windowHandler.getWindowUnsafe();
            AuthHttpRequest2.env = fetchedWindow === void 0 || fetchedWindow.fetch === void 0 ? global : fetchedWindow;
            AuthHttpRequest2.refreshTokenUrl = config.apiDomain + config.apiBasePath + "/session/refresh";
            AuthHttpRequest2.signOutUrl = config.apiDomain + config.apiBasePath + "/signout";
            AuthHttpRequest2.rid = "session";
            AuthHttpRequest2.config = config;
            if (AuthHttpRequest2.env.__supertokensOriginalFetch === void 0) {
              (0, logger_1.logDebugMessage)("init: __supertokensOriginalFetch is undefined");
              AuthHttpRequest2.env.__supertokensOriginalFetch = AuthHttpRequest2.env.fetch.bind(AuthHttpRequest2.env);
              AuthHttpRequest2.env.__supertokensSessionRecipe = recipeImpl;
              AuthHttpRequest2.env.fetch = AuthHttpRequest2.env.__supertokensSessionRecipe.addFetchInterceptorsAndReturnModifiedFetch({
                originalFetch: AuthHttpRequest2.env.__supertokensOriginalFetch,
                userContext: {}
              });
              AuthHttpRequest2.env.__supertokensSessionRecipe.addXMLHttpRequestInterceptor({
                userContext: {}
              });
            }
            AuthHttpRequest2.recipeImpl = AuthHttpRequest2.env.__supertokensSessionRecipe;
            AuthHttpRequest2.initCalled = true;
          };
          var _a;
          _a = AuthHttpRequest2;
          AuthHttpRequest2.initCalled = false;
          AuthHttpRequest2.doRequest = function(httpCall, config, url) {
            return __awaiter(void 0, void 0, void 0, function() {
              var doNotDoInterception, finalURL, origHeaders, accessToken, refreshToken, sessionRefreshAttempts, returnObj, preRequestLSS, clonedHeaders, configWithAntiCsrf, antiCsrfToken, transferMethod, response, errorMessage, retry;
              return __generator(_a, function(_b) {
                switch (_b.label) {
                  case 0:
                    if (!AuthHttpRequest2.initCalled) {
                      throw Error("init function not called");
                    }
                    (0, logger_1.logDebugMessage)("doRequest: start of fetch interception");
                    doNotDoInterception = false;
                    try {
                      finalURL = void 0;
                      if (typeof url === "string") {
                        finalURL = url;
                      } else if (typeof url === "object") {
                        if (typeof url.url === "string") {
                          finalURL = url.url;
                        } else if (typeof url.href === "string") {
                          finalURL = url.href;
                        }
                      }
                      doNotDoInterception = !AuthHttpRequest2.recipeImpl.shouldDoInterceptionBasedOnUrl(
                        finalURL,
                        AuthHttpRequest2.config.apiDomain,
                        AuthHttpRequest2.config.sessionTokenBackendDomain
                      );
                    } catch (err) {
                      if (err.message === "Please provide a valid domain name") {
                        (0, logger_1.logDebugMessage)(
                          "doRequest: Trying shouldDoInterceptionBasedOnUrl with location.origin"
                        );
                        doNotDoInterception = !AuthHttpRequest2.recipeImpl.shouldDoInterceptionBasedOnUrl(
                          windowHandler_1.default.getReferenceOrThrow().windowHandler.location.getOrigin(),
                          AuthHttpRequest2.config.apiDomain,
                          AuthHttpRequest2.config.sessionTokenBackendDomain
                        );
                      } else {
                        throw err;
                      }
                    }
                    (0, logger_1.logDebugMessage)("doRequest: Value of doNotDoInterception: " + doNotDoInterception);
                    if (!doNotDoInterception) return [3, 2];
                    (0, logger_1.logDebugMessage)("doRequest: Returning without interception");
                    return [4, httpCall(config)];
                  case 1:
                    return [2, _b.sent()];
                  case 2:
                    origHeaders = new Headers(
                      config !== void 0 && config.headers !== void 0 ? config.headers : url.headers
                    );
                    if (!origHeaders.has("Authorization")) return [3, 5];
                    return [4, getTokenForHeaderAuth("access")];
                  case 3:
                    accessToken = _b.sent();
                    return [4, getTokenForHeaderAuth("refresh")];
                  case 4:
                    refreshToken = _b.sent();
                    if (accessToken !== void 0 && refreshToken !== void 0 && origHeaders.get("Authorization") === "Bearer ".concat(accessToken)) {
                      (0, logger_1.logDebugMessage)(
                        "doRequest: Removing Authorization from user provided headers because it contains our access token"
                      );
                      origHeaders.delete("Authorization");
                    }
                    _b.label = 5;
                  case 5:
                    (0, logger_1.logDebugMessage)("doRequest: Interception started");
                    processState_1.ProcessState.getInstance().addState(
                      processState_1.PROCESS_STATE.CALLING_INTERCEPTION_REQUEST
                    );
                    sessionRefreshAttempts = 0;
                    returnObj = void 0;
                    _b.label = 6;
                  case 6:
                    if (false) return [3, 18];
                    return [4, getLocalSessionState(true)];
                  case 7:
                    preRequestLSS = _b.sent();
                    clonedHeaders = new Headers(origHeaders);
                    configWithAntiCsrf = __assign(__assign({}, config), { headers: clonedHeaders });
                    if (!(preRequestLSS.status === "EXISTS")) return [3, 9];
                    return [4, AntiCsrfToken.getToken(preRequestLSS.lastAccessTokenUpdate)];
                  case 8:
                    antiCsrfToken = _b.sent();
                    if (antiCsrfToken !== void 0) {
                      (0, logger_1.logDebugMessage)("doRequest: Adding anti-csrf token to request");
                      clonedHeaders.set("anti-csrf", antiCsrfToken);
                    }
                    _b.label = 9;
                  case 9:
                    if (AuthHttpRequest2.config.autoAddCredentials) {
                      (0, logger_1.logDebugMessage)("doRequest: Adding credentials include");
                      if (configWithAntiCsrf === void 0) {
                        configWithAntiCsrf = {
                          credentials: "include"
                        };
                      } else if (configWithAntiCsrf.credentials === void 0) {
                        configWithAntiCsrf = __assign(__assign({}, configWithAntiCsrf), {
                          credentials: "include"
                        });
                      }
                    }
                    if (!clonedHeaders.has("rid")) {
                      (0, logger_1.logDebugMessage)("doRequest: Adding rid header: anti-csrf");
                      clonedHeaders.set("rid", "anti-csrf");
                    } else {
                      (0, logger_1.logDebugMessage)("doRequest: rid header was already there in request");
                    }
                    transferMethod = AuthHttpRequest2.config.tokenTransferMethod;
                    (0, logger_1.logDebugMessage)("doRequest: Adding st-auth-mode header: " + transferMethod);
                    clonedHeaders.set("st-auth-mode", transferMethod);
                    return [4, setAuthorizationHeaderIfRequired(clonedHeaders)];
                  case 10:
                    _b.sent();
                    (0, logger_1.logDebugMessage)("doRequest: Making user's http call");
                    return [4, httpCall(configWithAntiCsrf)];
                  case 11:
                    response = _b.sent();
                    (0, logger_1.logDebugMessage)("doRequest: User's http call ended");
                    return [4, saveTokensFromHeaders(response)];
                  case 12:
                    _b.sent();
                    fireSessionUpdateEventsIfNecessary(
                      preRequestLSS.status === "EXISTS",
                      response.status,
                      response.headers.get("front-token")
                    );
                    if (!(response.status === AuthHttpRequest2.config.sessionExpiredStatusCode))
                      return [3, 14];
                    (0, logger_1.logDebugMessage)("doRequest: Status code is: " + response.status);
                    if (sessionRefreshAttempts >= AuthHttpRequest2.config.maxRetryAttemptsForSessionRefresh) {
                      (0, logger_1.logDebugMessage)(
                        "doRequest: Maximum session refresh attempts reached. sessionRefreshAttempts: ".concat(sessionRefreshAttempts, ", maxRetryAttemptsForSessionRefresh: ").concat(AuthHttpRequest2.config.maxRetryAttemptsForSessionRefresh)
                      );
                      errorMessage = "Received a 401 response from ".concat(
                        url,
                        ". Attempted to refresh the session and retry the request with the updated session tokens "
                      ).concat(
                        AuthHttpRequest2.config.maxRetryAttemptsForSessionRefresh,
                        " times, but each attempt resulted in a 401 error. The maximum session refresh limit has been reached. Please investigate your API. To increase the session refresh attempts, update maxRetryAttemptsForSessionRefresh in the config."
                      );
                      console.error(errorMessage);
                      throw new Error(errorMessage);
                    }
                    return [4, onUnauthorisedResponse(preRequestLSS)];
                  case 13:
                    retry = _b.sent();
                    sessionRefreshAttempts++;
                    (0, logger_1.logDebugMessage)("doRequest: sessionRefreshAttempts: " + sessionRefreshAttempts);
                    if (retry.result !== "RETRY") {
                      (0, logger_1.logDebugMessage)("doRequest: Not retrying original request");
                      if (retry.error !== void 0) {
                        if (retry.error instanceof Response) {
                          returnObj = retry.error;
                        } else {
                          throw retry.error;
                        }
                      } else {
                        returnObj = response;
                      }
                      return [3, 18];
                    }
                    (0, logger_1.logDebugMessage)("doRequest: Retrying original request");
                    return [3, 17];
                  case 14:
                    if (!(response.status === AuthHttpRequest2.config.invalidClaimStatusCode))
                      return [3, 16];
                    return [4, onInvalidClaimResponse(response)];
                  case 15:
                    _b.sent();
                    _b.label = 16;
                  case 16:
                    return [2, response];
                  case 17:
                    return [3, 6];
                  case 18:
                    return [2, returnObj];
                }
              });
            });
          };
          AuthHttpRequest2.attemptRefreshingSession = function() {
            return __awaiter(void 0, void 0, void 0, function() {
              var preRequestLSS, refresh;
              return __generator(_a, function(_b) {
                switch (_b.label) {
                  case 0:
                    if (!AuthHttpRequest2.initCalled) {
                      throw Error("init function not called");
                    }
                    return [4, getLocalSessionState(false)];
                  case 1:
                    preRequestLSS = _b.sent();
                    return [4, onUnauthorisedResponse(preRequestLSS)];
                  case 2:
                    refresh = _b.sent();
                    if (refresh.result === "API_ERROR") {
                      throw refresh.error;
                    }
                    return [2, refresh.result === "RETRY"];
                }
              });
            });
          };
          return AuthHttpRequest2;
        })()
      );
      exports.default = AuthHttpRequest;
      var LAST_ACCESS_TOKEN_UPDATE = "st-last-access-token-update";
      var REFRESH_TOKEN_NAME = "st-refresh-token";
      var ACCESS_TOKEN_NAME = "st-access-token";
      var ANTI_CSRF_NAME = "sAntiCsrf";
      var FRONT_TOKEN_NAME = "sFrontToken";
      function onUnauthorisedResponse(preRequestLSS) {
        return __awaiter(this, void 0, void 0, function() {
          var lock, postLockLSS, postLockSessionExists, preRequestSessionExists, sessionStatusChanged, accessTokenTimestampChanged, headers, antiCsrfToken, transferMethod, preAPIResult, response, isUnauthorised, errorMessage, error_1, postRequestLSS;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                return [4, lockFactory_1.default.getReferenceOrThrow().lockFactory()];
              case 1:
                lock = _b.sent();
                _b.label = 2;
              case 2:
                if (false) return [3, 23];
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: trying to acquire lock");
                return [4, lock.acquireLock("REFRESH_TOKEN_USE", 1e3)];
              case 3:
                if (!_b.sent()) return [3, 21];
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: lock acquired");
                _b.label = 4;
              case 4:
                _b.trys.push([4, 17, 19, 21]);
                return [4, getLocalSessionState(false)];
              case 5:
                postLockLSS = _b.sent();
                if (postLockLSS.status === "NOT_EXISTS") {
                  (0, logger_1.logDebugMessage)(
                    "onUnauthorisedResponse: Not refreshing because local session state is NOT_EXISTS"
                  );
                  AuthHttpRequest.config.onHandleEvent({
                    action: "UNAUTHORISED",
                    sessionExpiredOrRevoked: false,
                    userContext: {}
                  });
                  return [2, { result: "SESSION_EXPIRED" }];
                }
                postLockSessionExists = postLockLSS.status === "EXISTS";
                preRequestSessionExists = preRequestLSS.status === "EXISTS";
                sessionStatusChanged = postLockLSS.status !== preRequestLSS.status;
                accessTokenTimestampChanged = "lastAccessTokenUpdate" in postLockLSS && "lastAccessTokenUpdate" in preRequestLSS && postLockLSS.lastAccessTokenUpdate !== preRequestLSS.lastAccessTokenUpdate;
                if (sessionStatusChanged && postLockSessionExists) {
                  (0, logger_1.logDebugMessage)(
                    "onUnauthorisedResponse: Retrying early because session status has changed and postLockLSS.status is EXISTS"
                  );
                  return [2, { result: "RETRY" }];
                }
                if (postLockSessionExists && preRequestSessionExists && accessTokenTimestampChanged) {
                  (0, logger_1.logDebugMessage)(
                    "onUnauthorisedResponse: Retrying early because pre and post lastAccessTokenUpdate don't match"
                  );
                  return [2, { result: "RETRY" }];
                }
                headers = new Headers();
                if (!(preRequestLSS.status === "EXISTS")) return [3, 7];
                return [4, AntiCsrfToken.getToken(preRequestLSS.lastAccessTokenUpdate)];
              case 6:
                antiCsrfToken = _b.sent();
                if (antiCsrfToken !== void 0) {
                  (0, logger_1.logDebugMessage)(
                    "onUnauthorisedResponse: Adding anti-csrf token to refresh API call"
                  );
                  headers.set("anti-csrf", antiCsrfToken);
                }
                _b.label = 7;
              case 7:
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Adding rid and fdi-versions to refresh call header");
                headers.set("rid", AuthHttpRequest.rid);
                headers.set("fdi-version", version_1.supported_fdi.join(","));
                transferMethod = AuthHttpRequest.config.tokenTransferMethod;
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Adding st-auth-mode header: " + transferMethod);
                headers.set("st-auth-mode", transferMethod);
                return [4, setAuthorizationHeaderIfRequired(headers, true)];
              case 8:
                _b.sent();
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Calling refresh pre API hook");
                return [
                  4,
                  AuthHttpRequest.config.preAPIHook({
                    action: "REFRESH_SESSION",
                    requestInit: {
                      method: "post",
                      credentials: "include",
                      headers
                    },
                    url: AuthHttpRequest.refreshTokenUrl,
                    userContext: {}
                  })
                ];
              case 9:
                preAPIResult = _b.sent();
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Making refresh call");
                return [
                  4,
                  AuthHttpRequest.env.__supertokensOriginalFetch(preAPIResult.url, preAPIResult.requestInit)
                ];
              case 10:
                response = _b.sent();
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Refresh call ended");
                return [4, saveTokensFromHeaders(response)];
              case 11:
                _b.sent();
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Refresh status code is: " + response.status);
                isUnauthorised = response.status === AuthHttpRequest.config.sessionExpiredStatusCode;
                if (!(response.headers.get("front-token") === null)) return [3, 14];
                if (!isUnauthorised) return [3, 13];
                return [4, FrontToken.setItem("remove")];
              case 12:
                _b.sent();
                return [3, 14];
              case 13:
                if (response.status === 200) {
                  errorMessage = "The 'front-token' header is missing from a successful refresh-session response. The most likely causes are proxy settings (e.g.: 'front-token' missing from 'access-control-expose-headers' or a proxy stripping this header). Please investigate your API.";
                  console.error(errorMessage);
                  throw new Error(errorMessage);
                }
                _b.label = 14;
              case 14:
                fireSessionUpdateEventsIfNecessary(
                  preRequestLSS.status === "EXISTS",
                  response.status,
                  isUnauthorised && response.headers.get("front-token") === null ? "remove" : response.headers.get("front-token")
                );
                if (response.status >= 300) {
                  throw response;
                }
                return [
                  4,
                  AuthHttpRequest.config.postAPIHook({
                    action: "REFRESH_SESSION",
                    fetchResponse: response.clone(),
                    requestInit: preAPIResult.requestInit,
                    url: preAPIResult.url,
                    userContext: {}
                  })
                ];
              case 15:
                _b.sent();
                return [4, getLocalSessionState(false)];
              case 16:
                if (_b.sent().status === "NOT_EXISTS") {
                  (0, logger_1.logDebugMessage)(
                    "onUnauthorisedResponse: local session doesn't exist, so returning session expired"
                  );
                  return [2, { result: "SESSION_EXPIRED" }];
                }
                AuthHttpRequest.config.onHandleEvent({
                  action: "REFRESH_SESSION",
                  userContext: {}
                });
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Sending RETRY signal");
                return [2, { result: "RETRY" }];
              case 17:
                error_1 = _b.sent();
                return [4, getLocalSessionState(false)];
              case 18:
                if (_b.sent().status === "NOT_EXISTS") {
                  (0, logger_1.logDebugMessage)(
                    "onUnauthorisedResponse: local session doesn't exist, so returning session expired"
                  );
                  return [2, { result: "SESSION_EXPIRED", error: error_1 }];
                }
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: sending API_ERROR");
                return [2, { result: "API_ERROR", error: error_1 }];
              case 19:
                return [4, lock.releaseLock("REFRESH_TOKEN_USE")];
              case 20:
                _b.sent();
                (0, logger_1.logDebugMessage)("onUnauthorisedResponse: Released lock");
                return [
                  7
                  /*endfinally*/
                ];
              case 21:
                return [4, getLocalSessionState(false)];
              case 22:
                postRequestLSS = _b.sent();
                if (postRequestLSS.status === "NOT_EXISTS") {
                  (0, logger_1.logDebugMessage)(
                    "onUnauthorisedResponse: lock acquired failed and local session doesn't exist, so sending SESSION_EXPIRED"
                  );
                  return [2, { result: "SESSION_EXPIRED" }];
                } else {
                  if (postRequestLSS.status !== preRequestLSS.status || postRequestLSS.status === "EXISTS" && preRequestLSS.status === "EXISTS" && postRequestLSS.lastAccessTokenUpdate !== preRequestLSS.lastAccessTokenUpdate) {
                    (0, logger_1.logDebugMessage)(
                      "onUnauthorisedResponse: lock acquired failed and retrying early because pre and post lastAccessTokenUpdate don't match"
                    );
                    return [2, { result: "RETRY" }];
                  }
                }
                return [3, 2];
              case 23:
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      exports.onUnauthorisedResponse = onUnauthorisedResponse;
      function onTokenUpdate() {
        (0, logger_1.logDebugMessage)("onTokenUpdate: firing ACCESS_TOKEN_PAYLOAD_UPDATED event");
        AuthHttpRequest.config.onHandleEvent({
          action: "ACCESS_TOKEN_PAYLOAD_UPDATED",
          userContext: {}
        });
      }
      exports.onTokenUpdate = onTokenUpdate;
      function onInvalidClaimResponse(response) {
        return __awaiter(this, void 0, void 0, function() {
          var claimValidationErrors, _b;
          return __generator(this, function(_c) {
            switch (_c.label) {
              case 0:
                _c.trys.push([0, 2, , 3]);
                return [
                  4,
                  AuthHttpRequest.recipeImpl.getInvalidClaimsFromResponse({
                    response,
                    userContext: {}
                  })
                ];
              case 1:
                claimValidationErrors = _c.sent();
                if (claimValidationErrors) {
                  AuthHttpRequest.config.onHandleEvent({
                    action: "API_INVALID_CLAIM",
                    claimValidationErrors,
                    userContext: {}
                  });
                }
                return [3, 3];
              case 2:
                _b = _c.sent();
                return [3, 3];
              case 3:
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      exports.onInvalidClaimResponse = onInvalidClaimResponse;
      function getLocalSessionState(tryRefresh) {
        return __awaiter(this, void 0, void 0, function() {
          var lastAccessTokenUpdate, frontTokenExists, response, res, lastAccessTokenUpdate_1, frontTokenExists_1, errorMessage;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("getLocalSessionState: called");
                return [4, getFromCookies(LAST_ACCESS_TOKEN_UPDATE)];
              case 1:
                lastAccessTokenUpdate = _b.sent();
                return [4, FrontToken.doesTokenExists()];
              case 2:
                frontTokenExists = _b.sent();
                if (!(frontTokenExists && lastAccessTokenUpdate !== void 0)) return [3, 3];
                (0, logger_1.logDebugMessage)("getLocalSessionState: returning EXISTS since both frontToken and lastAccessTokenUpdate exists");
                return [2, { status: "EXISTS", lastAccessTokenUpdate }];
              case 3:
                if (!lastAccessTokenUpdate) return [3, 4];
                (0, logger_1.logDebugMessage)("getLocalSessionState: returning NOT_EXISTS since frontToken was cleared but lastAccessTokenUpdate exists");
                return [2, { status: "NOT_EXISTS" }];
              case 4:
                response = {
                  status: "MAY_EXIST"
                };
                if (!tryRefresh) return [3, 8];
                (0, logger_1.logDebugMessage)("getLocalSessionState: trying to refresh");
                return [4, onUnauthorisedResponse(response)];
              case 5:
                res = _b.sent();
                if (res.result !== "RETRY") {
                  (0, logger_1.logDebugMessage)(
                    "getLocalSessionState: return NOT_EXISTS in case error from backend" + res.result
                  );
                  return [
                    2,
                    {
                      status: "NOT_EXISTS"
                    }
                  ];
                }
                return [4, getFromCookies(LAST_ACCESS_TOKEN_UPDATE)];
              case 6:
                lastAccessTokenUpdate_1 = _b.sent();
                return [4, FrontToken.doesTokenExists()];
              case 7:
                frontTokenExists_1 = _b.sent();
                if (!frontTokenExists_1 || lastAccessTokenUpdate_1 === void 0) {
                  errorMessage = "Failed to retrieve local session state from cookies after a successful session refresh. This indicates a configuration error or that the browser is preventing cookie writes.";
                  console.error(errorMessage);
                  throw new Error(errorMessage);
                }
                (0, logger_1.logDebugMessage)("getLocalSessionState: returning EXISTS since both frontToken and lastAccessTokenUpdate exists post refresh");
                return [2, { status: "EXISTS", lastAccessTokenUpdate: lastAccessTokenUpdate_1 }];
              case 8:
                (0, logger_1.logDebugMessage)("getLocalSessionState: returning: " + response.status);
                return [2, response];
            }
          });
        });
      }
      exports.getLocalSessionState = getLocalSessionState;
      function getStorageNameForToken(tokenType) {
        switch (tokenType) {
          case "access":
            return ACCESS_TOKEN_NAME;
          case "refresh":
            return REFRESH_TOKEN_NAME;
        }
      }
      exports.getStorageNameForToken = getStorageNameForToken;
      function setToken(tokenType, value) {
        var name = getStorageNameForToken(tokenType);
        if (value !== "") {
          (0, logger_1.logDebugMessage)("setToken: saved ".concat(tokenType, " token into cookies"));
          return storeInCookies(name, value, Date.now() + 31536e5);
        } else {
          (0, logger_1.logDebugMessage)("setToken: cleared ".concat(tokenType, " token from cookies"));
          return storeInCookies(name, value, 0);
        }
      }
      exports.setToken = setToken;
      function storeInCookies(name, value, expiry) {
        var expires = "Fri, 31 Dec 9999 23:59:59 GMT";
        if (expiry !== Number.MAX_SAFE_INTEGER) {
          expires = new Date(expiry).toUTCString();
        }
        var domain = AuthHttpRequest.config.sessionTokenFrontendDomain;
        if (domain === "localhost" || domain === windowHandler_1.default.getReferenceOrThrow().windowHandler.location.getHostName()) {
          return cookieHandler_1.default.getReferenceOrThrow().cookieHandler.setCookie(
            "".concat(name, "=").concat(value, ";expires=").concat(expires, ";path=/;samesite=").concat(AuthHttpRequest.config.isInIframe ? "none;secure" : "lax")
          );
        } else {
          return cookieHandler_1.default.getReferenceOrThrow().cookieHandler.setCookie(
            "".concat(name, "=").concat(value, ";expires=").concat(expires, ";domain=").concat(domain, ";path=/;samesite=").concat(AuthHttpRequest.config.isInIframe ? "none;secure" : "lax")
          );
        }
      }
      function getTokenForHeaderAuth(tokenType) {
        return __awaiter(this, void 0, void 0, function() {
          var name;
          return __generator(this, function(_b) {
            name = getStorageNameForToken(tokenType);
            return [2, getFromCookies(name)];
          });
        });
      }
      exports.getTokenForHeaderAuth = getTokenForHeaderAuth;
      function getFromCookies(name) {
        return __awaiter(this, void 0, void 0, function() {
          var value, _b, parts, last;
          return __generator(this, function(_c) {
            switch (_c.label) {
              case 0:
                _b = "; ";
                return [4, cookieHandler_1.default.getReferenceOrThrow().cookieHandler.getCookie()];
              case 1:
                value = _b + _c.sent();
                parts = value.split("; " + name + "=");
                if (parts.length >= 2) {
                  last = parts.pop();
                  if (last !== void 0) {
                    return [2, last.split(";").shift()];
                  }
                }
                return [2, void 0];
            }
          });
        });
      }
      function setAuthorizationHeaderIfRequired(clonedHeaders, addRefreshToken) {
        if (addRefreshToken === void 0) {
          addRefreshToken = false;
        }
        return __awaiter(this, void 0, void 0, function() {
          var accessToken, refreshToken;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("setTokenHeaders: adding existing tokens as header");
                return [4, getTokenForHeaderAuth("access")];
              case 1:
                accessToken = _b.sent();
                return [4, getTokenForHeaderAuth("refresh")];
              case 2:
                refreshToken = _b.sent();
                if ((addRefreshToken || accessToken !== void 0) && refreshToken !== void 0) {
                  if (clonedHeaders.has("Authorization")) {
                    (0, logger_1.logDebugMessage)(
                      "setAuthorizationHeaderIfRequired: Authorization header defined by the user, not adding"
                    );
                  } else {
                    (0, logger_1.logDebugMessage)(
                      "setAuthorizationHeaderIfRequired: added authorization header"
                    );
                    clonedHeaders.set(
                      "Authorization",
                      "Bearer ".concat(addRefreshToken ? refreshToken : accessToken)
                    );
                  }
                } else {
                  (0, logger_1.logDebugMessage)(
                    "setAuthorizationHeaderIfRequired: token for header based auth not found"
                  );
                }
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      function saveTokensFromHeaders(response) {
        return __awaiter(this, void 0, void 0, function() {
          var refreshToken, accessToken, frontToken, antiCsrfToken, tok;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: Saving updated tokens from the response headers");
                refreshToken = response.headers.get("st-refresh-token");
                if (!(refreshToken !== null)) return [3, 2];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: saving new refresh token");
                return [4, setToken("refresh", refreshToken)];
              case 1:
                _b.sent();
                _b.label = 2;
              case 2:
                accessToken = response.headers.get("st-access-token");
                if (!(accessToken !== null)) return [3, 4];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: saving new access token");
                return [4, setToken("access", accessToken)];
              case 3:
                _b.sent();
                _b.label = 4;
              case 4:
                frontToken = response.headers.get("front-token");
                if (!(frontToken !== null)) return [3, 6];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: Setting sFrontToken: " + frontToken);
                return [4, FrontToken.setItem(frontToken)];
              case 5:
                _b.sent();
                (0, exports.updateClockSkewUsingFrontToken)({ frontToken, responseHeaders: response.headers });
                _b.label = 6;
              case 6:
                antiCsrfToken = response.headers.get("anti-csrf");
                if (!(antiCsrfToken !== null)) return [3, 9];
                return [4, getLocalSessionState(false)];
              case 7:
                tok = _b.sent();
                if (!(tok.status === "EXISTS")) return [3, 9];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: Setting anti-csrf token");
                return [4, AntiCsrfToken.setItem(tok.lastAccessTokenUpdate, antiCsrfToken)];
              case 8:
                _b.sent();
                _b.label = 9;
              case 9:
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      var successfullySavedToCookies = void 0;
      function saveLastAccessTokenUpdate() {
        return __awaiter(this, void 0, void 0, function() {
          var now;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("saveLastAccessTokenUpdate: called");
                now = Date.now().toString();
                (0, logger_1.logDebugMessage)("saveLastAccessTokenUpdate: setting " + now);
                return [4, storeInCookies(LAST_ACCESS_TOKEN_UPDATE, now, Number.MAX_SAFE_INTEGER)];
              case 1:
                _b.sent();
                if (!(successfullySavedToCookies === void 0)) return [3, 3];
                return [4, getFromCookies(LAST_ACCESS_TOKEN_UPDATE)];
              case 2:
                successfullySavedToCookies = _b.sent() === now;
                _b.label = 3;
              case 3:
                if (successfullySavedToCookies === false) {
                  console.warn(
                    "Saving to cookies was not successful, this indicates a configuration error or the browser preventing us from writing the cookies."
                  );
                }
                return [4, storeInCookies("sIRTFrontend", "", 0)];
              case 4:
                _b.sent();
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      exports.saveLastAccessTokenUpdate = saveLastAccessTokenUpdate;
      function getAntiCSRFToken() {
        return __awaiter(this, void 0, void 0, function() {
          function getAntiCSRFromCookie() {
            return __awaiter(this, void 0, void 0, function() {
              var value, _b, parts, last, temp;
              return __generator(this, function(_c) {
                switch (_c.label) {
                  case 0:
                    _b = "; ";
                    return [
                      4,
                      cookieHandler_1.default.getReferenceOrThrow().cookieHandler.getCookie()
                    ];
                  case 1:
                    value = _b + _c.sent();
                    parts = value.split("; " + ANTI_CSRF_NAME + "=");
                    if (parts.length >= 2) {
                      last = parts.pop();
                      if (last !== void 0) {
                        temp = last.split(";").shift();
                        if (temp === void 0) {
                          return [2, null];
                        }
                        return [2, temp];
                      }
                    }
                    return [2, null];
                }
              });
            });
          }
          var fromCookie;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("getAntiCSRFToken: called");
                return [4, getLocalSessionState(true)];
              case 1:
                if (!(_b.sent().status === "EXISTS")) {
                  (0, logger_1.logDebugMessage)(
                    "getAntiCSRFToken: Returning because local session state != EXISTS"
                  );
                  return [2, null];
                }
                return [4, getAntiCSRFromCookie()];
              case 2:
                fromCookie = _b.sent();
                (0, logger_1.logDebugMessage)("getAntiCSRFToken: returning: " + fromCookie);
                return [2, fromCookie];
            }
          });
        });
      }
      function setAntiCSRF(antiCSRFToken) {
        return __awaiter(this, void 0, void 0, function() {
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("setAntiCSRF: called: " + antiCSRFToken);
                if (!(antiCSRFToken !== void 0)) return [3, 2];
                return [4, storeInCookies(ANTI_CSRF_NAME, antiCSRFToken, Number.MAX_SAFE_INTEGER)];
              case 1:
                _b.sent();
                return [3, 4];
              case 2:
                return [4, storeInCookies(ANTI_CSRF_NAME, "", 0)];
              case 3:
                _b.sent();
                _b.label = 4;
              case 4:
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      exports.setAntiCSRF = setAntiCSRF;
      function getFrontTokenFromCookie() {
        return __awaiter(this, void 0, void 0, function() {
          var val;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("getFrontTokenFromCookie: called");
                return [4, getFromCookies(FRONT_TOKEN_NAME)];
              case 1:
                val = _b.sent();
                return [2, val === void 0 ? null : val];
            }
          });
        });
      }
      function parseFrontToken(frontToken) {
        return JSON.parse(decodeURIComponent(escape(atob(frontToken))));
      }
      function getFrontToken() {
        return __awaiter(this, void 0, void 0, function() {
          var fromCookie;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("getFrontToken: called");
                return [4, getLocalSessionState(true)];
              case 1:
                if (!(_b.sent().status === "EXISTS")) {
                  (0, logger_1.logDebugMessage)("getFrontToken: Returning because sIRTFrontend != EXISTS");
                  return [2, null];
                }
                return [4, getFrontTokenFromCookie()];
              case 2:
                fromCookie = _b.sent();
                (0, logger_1.logDebugMessage)("getFrontToken: returning: " + fromCookie);
                return [2, fromCookie];
            }
          });
        });
      }
      exports.getFrontToken = getFrontToken;
      function setFrontToken(frontToken) {
        return __awaiter(this, void 0, void 0, function() {
          var oldToken, oldPayload, newPayload;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("setFrontToken: called");
                return [4, getFrontTokenFromCookie()];
              case 1:
                oldToken = _b.sent();
                if (oldToken !== null && frontToken !== void 0) {
                  oldPayload = parseFrontToken(oldToken).up;
                  newPayload = parseFrontToken(frontToken).up;
                  if (JSON.stringify(oldPayload) !== JSON.stringify(newPayload)) {
                    onTokenUpdate();
                  }
                }
                if (!(frontToken === void 0)) return [3, 3];
                return [4, storeInCookies(FRONT_TOKEN_NAME, "", 0)];
              case 2:
                _b.sent();
                return [3, 5];
              case 3:
                return [4, storeInCookies(FRONT_TOKEN_NAME, frontToken, Number.MAX_SAFE_INTEGER)];
              case 4:
                _b.sent();
                _b.label = 5;
              case 5:
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      exports.setFrontToken = setFrontToken;
      function fireSessionUpdateEventsIfNecessary(wasLoggedIn, status, frontTokenHeaderFromResponse) {
        if (frontTokenHeaderFromResponse === void 0 || frontTokenHeaderFromResponse === null) {
          (0, logger_1.logDebugMessage)(
            "fireSessionUpdateEventsIfNecessary returning early because the front token was not updated"
          );
          return;
        }
        var frontTokenExistsAfter = frontTokenHeaderFromResponse !== "remove";
        (0, logger_1.logDebugMessage)(
          "fireSessionUpdateEventsIfNecessary wasLoggedIn: ".concat(wasLoggedIn, " frontTokenExistsAfter: ").concat(frontTokenExistsAfter, " status: ").concat(status)
        );
        if (wasLoggedIn) {
          if (!frontTokenExistsAfter) {
            if (status === AuthHttpRequest.config.sessionExpiredStatusCode) {
              (0, logger_1.logDebugMessage)("onUnauthorisedResponse: firing UNAUTHORISED event");
              AuthHttpRequest.config.onHandleEvent({
                action: "UNAUTHORISED",
                sessionExpiredOrRevoked: true,
                userContext: {}
              });
            } else {
              (0, logger_1.logDebugMessage)("onUnauthorisedResponse: firing SIGN_OUT event");
              AuthHttpRequest.config.onHandleEvent({
                action: "SIGN_OUT",
                userContext: {}
              });
            }
          }
        } else if (frontTokenExistsAfter) {
          (0, logger_1.logDebugMessage)("onUnauthorisedResponse: firing SESSION_CREATED event");
          AuthHttpRequest.config.onHandleEvent({
            action: "SESSION_CREATED",
            userContext: {}
          });
        }
      }
      exports.fireSessionUpdateEventsIfNecessary = fireSessionUpdateEventsIfNecessary;
      var updateClockSkewUsingFrontToken = function(_b) {
        var frontToken = _b.frontToken, responseHeaders = _b.responseHeaders;
        (0, logger_1.logDebugMessage)("updateClockSkewUsingFrontToken: frontToken: " + frontToken);
        if (frontToken === null || frontToken === void 0 || frontToken === "remove") {
          (0, logger_1.logDebugMessage)(
            "updateClockSkewUsingFrontToken: the access token payload wasn't updated or is being removed, skipping clock skew update"
          );
          return;
        }
        var frontTokenPayload = parseFrontToken(frontToken);
        var clockSkewInMillis = AuthHttpRequest.recipeImpl.calculateClockSkewInMillis({
          accessTokenPayload: frontTokenPayload.up,
          responseHeaders
        });
        dateProvider_1.default.getReferenceOrThrow().dateProvider.setClientClockSkewInMillis(clockSkewInMillis);
        (0, logger_1.logDebugMessage)("updateClockSkewUsingFrontToken: Client clock synchronized successfully");
      };
      exports.updateClockSkewUsingFrontToken = updateClockSkewUsingFrontToken;
    }
  });

  // node_modules/supertokens-website/lib/build/utils/sessionClaimValidatorStore.js
  var require_sessionClaimValidatorStore = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/sessionClaimValidatorStore.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.SessionClaimValidatorStore = void 0;
      var SessionClaimValidatorStore = (
        /** @class */
        (function() {
          function SessionClaimValidatorStore2() {
          }
          SessionClaimValidatorStore2.claimValidatorsAddedByOtherRecipes = [];
          SessionClaimValidatorStore2.addClaimValidatorFromOtherRecipe = function(builder) {
            SessionClaimValidatorStore2.claimValidatorsAddedByOtherRecipes.push(builder);
          };
          SessionClaimValidatorStore2.getClaimValidatorsAddedByOtherRecipes = function() {
            return SessionClaimValidatorStore2.claimValidatorsAddedByOtherRecipes;
          };
          return SessionClaimValidatorStore2;
        })()
      );
      exports.SessionClaimValidatorStore = SessionClaimValidatorStore;
      exports.default = SessionClaimValidatorStore;
    }
  });

  // node_modules/supertokens-website/lib/build/utils/globalClaimValidators.js
  var require_globalClaimValidators = __commonJS({
    "node_modules/supertokens-website/lib/build/utils/globalClaimValidators.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.getGlobalClaimValidators = void 0;
      var _1 = require_utils();
      var fetch_1 = require_fetch();
      var sessionClaimValidatorStore_1 = require_sessionClaimValidatorStore();
      function getGlobalClaimValidators(overrideGlobalClaimValidators, userContext) {
        var normalisedUserContext = (0, _1.getNormalisedUserContext)(userContext);
        var claimValidatorsAddedByOtherRecipes = sessionClaimValidatorStore_1.default.getClaimValidatorsAddedByOtherRecipes();
        var globalClaimValidators = fetch_1.default.recipeImpl.getGlobalClaimValidators({
          claimValidatorsAddedByOtherRecipes,
          userContext: normalisedUserContext
        });
        var claimValidators = overrideGlobalClaimValidators !== void 0 ? overrideGlobalClaimValidators(globalClaimValidators, normalisedUserContext) : globalClaimValidators;
        return claimValidators;
      }
      exports.getGlobalClaimValidators = getGlobalClaimValidators;
    }
  });

  // node_modules/supertokens-website/utils/globalClaimValidators/index.js
  var require_globalClaimValidators2 = __commonJS({
    "node_modules/supertokens-website/utils/globalClaimValidators/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      __export2(require_globalClaimValidators());
    }
  });

  // node_modules/supertokens-web-js/lib/build/utils.js
  var require_utils2 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/utils.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.getPublicConfig = exports.getPublicPlugin = exports.applyPlugins = exports.normaliseUser = exports.normaliseUserResponse = exports.getGlobalClaimValidators = exports.getHashFromLocation = exports.getNormalisedUserContext = exports.checkForSSRErrorAndAppendIfNeeded = exports.getAllQueryParams = exports.getQueryParams = exports.isTest = exports.normaliseInputAppInfoOrThrowError = exports.appendQueryParamsToURL = void 0;
      var windowHandler_1 = require_windowHandler3();
      var constants_1 = require_constants();
      var normalisedURLDomain_1 = require_normalisedURLDomain();
      var normalisedURLPath_1 = require_normalisedURLPath();
      var types_1 = require_types();
      var globalClaimValidators_1 = require_globalClaimValidators2();
      function appendQueryParamsToURL(stringUrl, queryParams) {
        if (queryParams === void 0) {
          return stringUrl;
        }
        try {
          var url_1 = new URL(stringUrl);
          Object.entries(queryParams).forEach(function(_a) {
            var key = _a[0], value = _a[1];
            url_1.searchParams.set(key, value);
          });
          return url_1.href;
        } catch (e) {
          var fakeDomain = stringUrl.startsWith("/") ? "http:localhost" : "http://localhost/";
          var url_2 = new URL("".concat(fakeDomain).concat(stringUrl));
          Object.entries(queryParams).forEach(function(_a) {
            var key = _a[0], value = _a[1];
            url_2.searchParams.set(key, value);
          });
          return "".concat(url_2.pathname).concat(url_2.search);
        }
      }
      exports.appendQueryParamsToURL = appendQueryParamsToURL;
      function getNormalisedURLPathOrDefault(defaultPath, path) {
        if (path !== void 0) {
          return new normalisedURLPath_1.default(path);
        } else {
          return new normalisedURLPath_1.default(defaultPath);
        }
      }
      function normaliseInputAppInfoOrThrowError(appInfo) {
        if (appInfo === void 0) {
          throw new Error("Please provide the appInfo object when calling supertokens.init");
        }
        if (appInfo.apiDomain === void 0) {
          throw new Error("Please provide your apiDomain inside the appInfo object when calling supertokens.init");
        }
        if (appInfo.appName === void 0) {
          throw new Error("Please provide your appName inside the appInfo object when calling supertokens.init");
        }
        var apiGatewayPath = new normalisedURLPath_1.default("");
        if (appInfo.apiGatewayPath !== void 0) {
          apiGatewayPath = new normalisedURLPath_1.default(appInfo.apiGatewayPath);
        }
        return {
          appName: appInfo.appName,
          apiDomain: new normalisedURLDomain_1.default(appInfo.apiDomain),
          apiBasePath: apiGatewayPath.appendPath(
            getNormalisedURLPathOrDefault(constants_1.DEFAULT_API_BASE_PATH, appInfo.apiBasePath)
          )
        };
      }
      exports.normaliseInputAppInfoOrThrowError = normaliseInputAppInfoOrThrowError;
      function isTest() {
        try {
          return process.env.TEST_MODE === "testing";
        } catch (err) {
          return false;
        }
      }
      exports.isTest = isTest;
      function getQueryParams(param) {
        var urlParams = new URLSearchParams(
          windowHandler_1.WindowHandlerReference.getReferenceOrThrow().windowHandler.location.getSearch()
        );
        var queryParam = urlParams.get(param);
        if (queryParam === null) {
          return void 0;
        }
        return queryParam;
      }
      exports.getQueryParams = getQueryParams;
      function getAllQueryParams() {
        return new URLSearchParams(
          windowHandler_1.WindowHandlerReference.getReferenceOrThrow().windowHandler.location.getSearch()
        );
      }
      exports.getAllQueryParams = getAllQueryParams;
      function checkForSSRErrorAndAppendIfNeeded(error) {
        if (typeof window === "undefined") {
          error = error + constants_1.SSR_ERROR;
        }
        return error;
      }
      exports.checkForSSRErrorAndAppendIfNeeded = checkForSSRErrorAndAppendIfNeeded;
      function getNormalisedUserContext(userContext) {
        return userContext === void 0 ? {} : userContext;
      }
      exports.getNormalisedUserContext = getNormalisedUserContext;
      function getHashFromLocation() {
        return windowHandler_1.WindowHandlerReference.getReferenceOrThrow().windowHandler.location.getHash().substring(1);
      }
      exports.getHashFromLocation = getHashFromLocation;
      function getGlobalClaimValidators(_a) {
        var overrideGlobalClaimValidators = _a.overrideGlobalClaimValidators, userContext = _a.userContext;
        return (0, globalClaimValidators_1.getGlobalClaimValidators)(overrideGlobalClaimValidators, userContext);
      }
      exports.getGlobalClaimValidators = getGlobalClaimValidators;
      function normaliseUserResponse(recipeId, response) {
        if ("createdNewRecipeUser" in response) {
          return response;
        }
        return {
          createdNewRecipeUser: response.createdNewUser,
          user: normaliseUser(recipeId, response.user)
        };
      }
      exports.normaliseUserResponse = normaliseUserResponse;
      function normaliseUser(recipeId, responseUser) {
        if ("loginMethods" in responseUser) {
          return responseUser;
        }
        var emails = responseUser.email !== void 0 ? [responseUser.email] : [];
        var phoneNumbers = responseUser.phoneNumber !== void 0 ? [responseUser.phoneNumber] : [];
        var thirdParty = responseUser.thirdParty !== void 0 ? [responseUser.thirdParty] : [];
        var webauthn = responseUser.webauthn !== void 0 ? responseUser.webauthn : { credentialIds: [] };
        return {
          id: responseUser.id,
          emails,
          phoneNumbers,
          thirdParty,
          webauthn,
          isPrimaryUser: false,
          tenantIds: responseUser.tenantIds,
          timeJoined: responseUser.timeJoined,
          loginMethods: [
            {
              recipeId,
              recipeUserId: responseUser.id,
              timeJoined: responseUser.timeJoined,
              tenantIds: responseUser.tenantIds,
              email: responseUser.email,
              phoneNumber: responseUser.email
            }
          ]
        };
      }
      exports.normaliseUser = normaliseUser;
      function applyPlugins(recipeId, config, plugins) {
        var _a;
        var _config = __assign({}, config !== null && config !== void 0 ? config : {});
        var functionLayers = [(_a = _config.override) === null || _a === void 0 ? void 0 : _a.functions];
        for (var _i = 0, plugins_1 = plugins; _i < plugins_1.length; _i++) {
          var plugin = plugins_1[_i];
          var overrides = plugin[recipeId];
          if (overrides) {
            _config = __assign({}, overrides.config ? overrides.config(_config) : _config);
            if (overrides.functions !== void 0) {
              functionLayers.push(overrides.functions);
            }
          }
        }
        functionLayers = functionLayers.reverse().filter(function(layer) {
          return layer !== void 0;
        });
        if (functionLayers.length > 0) {
          _config.override = __assign(__assign({}, _config.override), {
            functions: function(oI, builder) {
              for (var _i2 = 0, functionLayers_1 = functionLayers; _i2 < functionLayers_1.length; _i2++) {
                var layer = functionLayers_1[_i2];
                builder.override(layer);
              }
              return oI;
            }
          });
        }
        return _config;
      }
      exports.applyPlugins = applyPlugins;
      function getPublicPlugin(plugin) {
        return {
          id: plugin.id,
          initialized: plugin.init ? false : true,
          version: plugin.version,
          exports: plugin.exports,
          compatibleWebJSSDKVersions: plugin.compatibleWebJSSDKVersions
        };
      }
      exports.getPublicPlugin = getPublicPlugin;
      function getPublicConfig(config) {
        var configKeys = Object.keys(config);
        var publicConfig = configKeys.reduce(function(acc, key) {
          var _a;
          if (types_1.nonPublicConfigProperties.includes(key)) {
            return acc;
          } else {
            return __assign(__assign({}, acc), (_a = {}, _a[key] = config[key], _a));
          }
        }, {});
        return publicConfig;
      }
      exports.getPublicConfig = getPublicConfig;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/recipeModule/index.js
  var require_recipeModule = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/recipeModule/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      var RecipeModule = (
        /** @class */
        /* @__PURE__ */ (function() {
          function RecipeModule2(config) {
            this.config = config;
          }
          return RecipeModule2;
        })()
      );
      exports.default = RecipeModule;
    }
  });

  // node_modules/supertokens-website/lib/build/axiosError.js
  var require_axiosError = __commonJS({
    "node_modules/supertokens-website/lib/build/axiosError.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.createAxiosErrorFromFetchResp = void 0;
      function enhanceAxiosError(error, config, code, request2, response) {
        error.config = config;
        if (code) {
          error.code = code;
        }
        error.request = request2;
        error.response = response;
        error.isAxiosError = true;
        error.toJSON = function toJSON() {
          return {
            // Standard
            message: this.message,
            name: this.name,
            // Microsoft
            description: this.description,
            number: this.number,
            // Mozilla
            fileName: this.fileName,
            lineNumber: this.lineNumber,
            columnNumber: this.columnNumber,
            stack: this.stack,
            // Axios
            config: this.config,
            code: this.code
          };
        };
        return error;
      }
      function createAxiosErrorFromFetchResp(responseOrError) {
        return __awaiter(this, void 0, void 0, function() {
          var config, isResponse, axiosResponse, contentType, data, _a;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                config = {
                  url: responseOrError.url,
                  headers: responseOrError.headers
                };
                isResponse = "status" in responseOrError;
                if (!isResponse) return [3, 12];
                contentType = responseOrError.headers.get("content-type");
                data = void 0;
                if (!(contentType === null)) return [3, 5];
                _b.label = 1;
              case 1:
                _b.trys.push([1, 3, , 4]);
                return [4, responseOrError.text()];
              case 2:
                data = _b.sent();
                return [3, 4];
              case 3:
                _a = _b.sent();
                data = "";
                return [3, 4];
              case 4:
                return [3, 11];
              case 5:
                if (!contentType.includes("application/json")) return [3, 7];
                return [4, responseOrError.json()];
              case 6:
                data = _b.sent();
                return [3, 11];
              case 7:
                if (!contentType.includes("text/")) return [3, 9];
                return [4, responseOrError.text()];
              case 8:
                data = _b.sent();
                return [3, 11];
              case 9:
                return [4, responseOrError.blob()];
              case 10:
                data = _b.sent();
                _b.label = 11;
              case 11:
                axiosResponse = {
                  data,
                  status: responseOrError.status,
                  statusText: responseOrError.statusText,
                  headers: responseOrError.headers,
                  config,
                  request: void 0
                };
                _b.label = 12;
              case 12:
                return [
                  2,
                  enhanceAxiosError(
                    isResponse ? new Error("Request failed with status code " + responseOrError.status) : responseOrError,
                    config,
                    responseOrError.code,
                    void 0,
                    axiosResponse
                  )
                ];
            }
          });
        });
      }
      exports.createAxiosErrorFromFetchResp = createAxiosErrorFromFetchResp;
    }
  });

  // node_modules/supertokens-website/lib/build/axios.js
  var require_axios = __commonJS({
    "node_modules/supertokens-website/lib/build/axios.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.responseErrorInterceptor = exports.responseInterceptor = exports.interceptorFunctionRequestFulfilled = void 0;
      var axiosError_1 = require_axiosError();
      var fetch_1 = require_fetch();
      var processState_1 = require_processState();
      var windowHandler_1 = require_windowHandler();
      var logger_1 = require_logger();
      function incrementSessionRefreshAttemptCount(config) {
        if (config.__supertokensSessionRefreshAttempts === void 0) {
          config.__supertokensSessionRefreshAttempts = 0;
        }
        config.__supertokensSessionRefreshAttempts++;
      }
      function hasExceededMaxSessionRefreshAttempts(config) {
        if (config.__supertokensSessionRefreshAttempts === void 0) {
          config.__supertokensSessionRefreshAttempts = 0;
        }
        return config.__supertokensSessionRefreshAttempts >= fetch_1.default.config.maxRetryAttemptsForSessionRefresh;
      }
      function getUrlFromConfig(config) {
        var url = config.url === void 0 ? "" : config.url;
        var baseURL = config.baseURL;
        if (baseURL !== void 0) {
          if (url.charAt(0) === "/" && baseURL.charAt(baseURL.length - 1) === "/") {
            url = baseURL + url.substr(1);
          } else if (url.charAt(0) !== "/" && baseURL.charAt(baseURL.length - 1) !== "/") {
            url = baseURL + "/" + url;
          } else {
            url = baseURL + url;
          }
        }
        return url;
      }
      function interceptorFunctionRequestFulfilled(config) {
        return __awaiter(this, void 0, void 0, function() {
          var url, doNotDoInterception, preRequestLSS, configWithAntiCsrf, antiCsrfToken, transferMethod;
          return __generator(this, function(_a) {
            switch (_a.label) {
              case 0:
                (0, logger_1.logDebugMessage)("interceptorFunctionRequestFulfilled: started axios interception");
                url = getUrlFromConfig(config);
                doNotDoInterception = false;
                try {
                  doNotDoInterception = typeof url === "string" && !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                    url,
                    fetch_1.default.config.apiDomain,
                    fetch_1.default.config.sessionTokenBackendDomain
                  );
                } catch (err) {
                  if (err.message === "Please provide a valid domain name") {
                    (0, logger_1.logDebugMessage)(
                      "interceptorFunctionRequestFulfilled: Trying shouldDoInterceptionBasedOnUrl with location.origin"
                    );
                    doNotDoInterception = !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                      windowHandler_1.default.getReferenceOrThrow().windowHandler.location.getOrigin(),
                      fetch_1.default.config.apiDomain,
                      fetch_1.default.config.sessionTokenBackendDomain
                    );
                  } else {
                    throw err;
                  }
                }
                (0, logger_1.logDebugMessage)("interceptorFunctionRequestFulfilled: Value of doNotDoInterception: " + doNotDoInterception);
                if (doNotDoInterception) {
                  (0, logger_1.logDebugMessage)(
                    "interceptorFunctionRequestFulfilled: Returning config unchanged"
                  );
                  return [2, config];
                }
                (0, logger_1.logDebugMessage)("interceptorFunctionRequestFulfilled: Modifying config");
                processState_1.ProcessState.getInstance().addState(
                  processState_1.PROCESS_STATE.CALLING_INTERCEPTION_REQUEST
                );
                return [4, (0, fetch_1.getLocalSessionState)(true)];
              case 1:
                preRequestLSS = _a.sent();
                configWithAntiCsrf = config;
                if (!(preRequestLSS.status === "EXISTS")) return [3, 3];
                return [4, fetch_1.AntiCsrfToken.getToken(preRequestLSS.lastAccessTokenUpdate)];
              case 2:
                antiCsrfToken = _a.sent();
                if (antiCsrfToken !== void 0) {
                  (0, logger_1.logDebugMessage)(
                    "interceptorFunctionRequestFulfilled: Adding anti-csrf token to request"
                  );
                  configWithAntiCsrf = __assign(__assign({}, configWithAntiCsrf), {
                    headers: configWithAntiCsrf === void 0 ? {
                      "anti-csrf": antiCsrfToken
                    } : __assign(__assign({}, configWithAntiCsrf.headers), { "anti-csrf": antiCsrfToken })
                  });
                }
                _a.label = 3;
              case 3:
                if (fetch_1.default.config.autoAddCredentials && configWithAntiCsrf.withCredentials === void 0) {
                  (0, logger_1.logDebugMessage)(
                    "interceptorFunctionRequestFulfilled: Adding credentials include"
                  );
                  configWithAntiCsrf = __assign(__assign({}, configWithAntiCsrf), { withCredentials: true });
                }
                (0, logger_1.logDebugMessage)("interceptorFunctionRequestFulfilled: Adding rid header: anti-csrf (it may be overriden by the user's provided rid)");
                configWithAntiCsrf = __assign(__assign({}, configWithAntiCsrf), {
                  headers: configWithAntiCsrf === void 0 ? {
                    rid: "anti-csrf"
                  } : __assign({ rid: "anti-csrf" }, configWithAntiCsrf.headers)
                });
                transferMethod = fetch_1.default.config.tokenTransferMethod;
                (0, logger_1.logDebugMessage)("interceptorFunctionRequestFulfilled: Adding st-auth-mode header: " + transferMethod);
                configWithAntiCsrf.headers["st-auth-mode"] = transferMethod;
                return [4, removeAuthHeaderIfMatchesLocalToken(configWithAntiCsrf)];
              case 4:
                configWithAntiCsrf = _a.sent();
                return [4, setAuthorizationHeaderIfRequired(configWithAntiCsrf)];
              case 5:
                _a.sent();
                (0, logger_1.logDebugMessage)("interceptorFunctionRequestFulfilled: returning modified config");
                return [2, configWithAntiCsrf];
            }
          });
        });
      }
      exports.interceptorFunctionRequestFulfilled = interceptorFunctionRequestFulfilled;
      function responseInterceptor(axiosInstance) {
        var _this = this;
        return function(response) {
          return __awaiter(_this, void 0, void 0, function() {
            var doNotDoInterception, url, preRequestLSS, config;
            return __generator(this, function(_a) {
              switch (_a.label) {
                case 0:
                  doNotDoInterception = false;
                  if (!fetch_1.default.initCalled) {
                    throw new Error("init function not called");
                  }
                  (0, logger_1.logDebugMessage)("responseInterceptor: started");
                  (0, logger_1.logDebugMessage)("responseInterceptor: already intercepted: " + response.headers["x-supertokens-xhr-intercepted"]);
                  url = getUrlFromConfig(response.config);
                  try {
                    doNotDoInterception = typeof url === "string" && !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                      url,
                      fetch_1.default.config.apiDomain,
                      fetch_1.default.config.sessionTokenBackendDomain
                    ) || !!response.headers["x-supertokens-xhr-intercepted"];
                  } catch (err) {
                    if (err.message === "Please provide a valid domain name") {
                      (0, logger_1.logDebugMessage)(
                        "responseInterceptor: Trying shouldDoInterceptionBasedOnUrl with location.origin"
                      );
                      doNotDoInterception = !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                        windowHandler_1.default.getReferenceOrThrow().windowHandler.location.getOrigin(),
                        fetch_1.default.config.apiDomain,
                        fetch_1.default.config.sessionTokenBackendDomain
                      ) || !!response.headers["x-supertokens-xhr-intercepted"];
                    } else {
                      throw err;
                    }
                  }
                  (0, logger_1.logDebugMessage)("responseInterceptor: Value of doNotDoInterception: " + doNotDoInterception);
                  if (doNotDoInterception) {
                    (0, logger_1.logDebugMessage)("responseInterceptor: Returning without interception");
                    return [2, response];
                  }
                  (0, logger_1.logDebugMessage)("responseInterceptor: Interception started");
                  processState_1.ProcessState.getInstance().addState(
                    processState_1.PROCESS_STATE.CALLING_INTERCEPTION_RESPONSE
                  );
                  return [4, (0, fetch_1.getLocalSessionState)(false)];
                case 1:
                  preRequestLSS = _a.sent();
                  return [4, saveTokensFromHeaders(response)];
                case 2:
                  _a.sent();
                  (0, fetch_1.fireSessionUpdateEventsIfNecessary)(preRequestLSS.status === "EXISTS", response.status, response.headers["front-token"]);
                  if (!(response.status === fetch_1.default.config.sessionExpiredStatusCode))
                    return [3, 3];
                  (0, logger_1.logDebugMessage)("responseInterceptor: Status code is: " + response.status);
                  config = response.config;
                  return [
                    2,
                    AuthHttpRequest.doRequest(
                      function(config2) {
                        return axiosInstance(config2);
                      },
                      config,
                      url,
                      response,
                      void 0,
                      true
                    )
                  ];
                case 3:
                  if (!(response.status === fetch_1.default.config.invalidClaimStatusCode))
                    return [3, 5];
                  return [4, (0, fetch_1.onInvalidClaimResponse)(response)];
                case 4:
                  _a.sent();
                  _a.label = 5;
                case 5:
                  return [2, response];
              }
            });
          });
        };
      }
      exports.responseInterceptor = responseInterceptor;
      function responseErrorInterceptor(axiosInstance) {
        var _this = this;
        return function(error) {
          return __awaiter(_this, void 0, void 0, function() {
            var config;
            return __generator(this, function(_a) {
              switch (_a.label) {
                case 0:
                  (0, logger_1.logDebugMessage)("responseErrorInterceptor: called");
                  (0, logger_1.logDebugMessage)("responseErrorInterceptor: already intercepted: " + (error.response && error.response.headers["x-supertokens-xhr-intercepted"]));
                  if (error.response === void 0 || error.response.headers["x-supertokens-xhr-intercepted"]) {
                    throw error;
                  }
                  if (!(error.response !== void 0 && error.response.status === fetch_1.default.config.sessionExpiredStatusCode))
                    return [3, 1];
                  (0, logger_1.logDebugMessage)("responseErrorInterceptor: Status code is: " + error.response.status);
                  config = error.config;
                  return [
                    2,
                    AuthHttpRequest.doRequest(
                      function(config2) {
                        return axiosInstance(config2);
                      },
                      config,
                      getUrlFromConfig(config),
                      void 0,
                      error,
                      true
                    )
                  ];
                case 1:
                  if (!(error.response !== void 0 && error.response.status === fetch_1.default.config.invalidClaimStatusCode))
                    return [3, 3];
                  return [4, (0, fetch_1.onInvalidClaimResponse)(error.response)];
                case 2:
                  _a.sent();
                  _a.label = 3;
                case 3:
                  throw error;
              }
            });
          });
        };
      }
      exports.responseErrorInterceptor = responseErrorInterceptor;
      var AuthHttpRequest = (
        /** @class */
        (function() {
          function AuthHttpRequest2() {
          }
          var _a;
          _a = AuthHttpRequest2;
          AuthHttpRequest2.doRequest = function(httpCall, config, url, prevResponse, prevError, viaInterceptor) {
            if (viaInterceptor === void 0) {
              viaInterceptor = false;
            }
            return __awaiter(void 0, void 0, void 0, function() {
              var doNotDoInterception, returnObj, preRequestLSS, configWithAntiCsrf, antiCsrfToken, transferMethod, localPrevError, localPrevResponse, response, _b, err_1, response, errorMessage, refreshResult, _c;
              return __generator(_a, function(_d) {
                switch (_d.label) {
                  case 0:
                    if (!fetch_1.default.initCalled) {
                      throw Error("init function not called");
                    }
                    (0, logger_1.logDebugMessage)("doRequest: called");
                    doNotDoInterception = false;
                    try {
                      doNotDoInterception = typeof url === "string" && !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                        url,
                        fetch_1.default.config.apiDomain,
                        fetch_1.default.config.sessionTokenBackendDomain
                      ) && viaInterceptor;
                    } catch (err) {
                      if (err.message === "Please provide a valid domain name") {
                        (0, logger_1.logDebugMessage)(
                          "doRequest: Trying shouldDoInterceptionBasedOnUrl with location.origin"
                        );
                        doNotDoInterception = !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                          windowHandler_1.default.getReferenceOrThrow().windowHandler.location.getOrigin(),
                          fetch_1.default.config.apiDomain,
                          fetch_1.default.config.sessionTokenBackendDomain
                        ) && viaInterceptor;
                      } else {
                        throw err;
                      }
                    }
                    (0, logger_1.logDebugMessage)("doRequest: Value of doNotDoInterception: " + doNotDoInterception);
                    if (!doNotDoInterception) return [3, 2];
                    (0, logger_1.logDebugMessage)("doRequest: Returning without interception");
                    if (prevError !== void 0) {
                      throw prevError;
                    } else if (prevResponse !== void 0) {
                      return [2, prevResponse];
                    }
                    return [4, httpCall(config)];
                  case 1:
                    return [2, _d.sent()];
                  case 2:
                    (0, logger_1.logDebugMessage)("doRequest: Interception started");
                    return [4, removeAuthHeaderIfMatchesLocalToken(config)];
                  case 3:
                    config = _d.sent();
                    returnObj = void 0;
                    _d.label = 4;
                  case 4:
                    if (false) return [3, 28];
                    return [4, (0, fetch_1.getLocalSessionState)(true)];
                  case 5:
                    preRequestLSS = _d.sent();
                    configWithAntiCsrf = config;
                    if (!(preRequestLSS.status === "EXISTS")) return [3, 7];
                    return [4, fetch_1.AntiCsrfToken.getToken(preRequestLSS.lastAccessTokenUpdate)];
                  case 6:
                    antiCsrfToken = _d.sent();
                    if (antiCsrfToken !== void 0) {
                      (0, logger_1.logDebugMessage)("doRequest: Adding anti-csrf token to request");
                      configWithAntiCsrf = __assign(__assign({}, configWithAntiCsrf), {
                        headers: configWithAntiCsrf === void 0 ? {
                          "anti-csrf": antiCsrfToken
                        } : __assign(__assign({}, configWithAntiCsrf.headers), {
                          "anti-csrf": antiCsrfToken
                        })
                      });
                    }
                    _d.label = 7;
                  case 7:
                    if (fetch_1.default.config.autoAddCredentials && configWithAntiCsrf.withCredentials === void 0) {
                      (0, logger_1.logDebugMessage)("doRequest: Adding credentials include");
                      configWithAntiCsrf = __assign(__assign({}, configWithAntiCsrf), { withCredentials: true });
                    }
                    (0, logger_1.logDebugMessage)("doRequest: Adding rid header: anti-csrf (May get overriden by user's rid)");
                    configWithAntiCsrf = __assign(__assign({}, configWithAntiCsrf), {
                      headers: configWithAntiCsrf === void 0 ? {
                        rid: "anti-csrf"
                      } : __assign({ rid: "anti-csrf" }, configWithAntiCsrf.headers)
                    });
                    transferMethod = fetch_1.default.config.tokenTransferMethod;
                    (0, logger_1.logDebugMessage)("doRequest: Adding st-auth-mode header: " + transferMethod);
                    configWithAntiCsrf.headers["st-auth-mode"] = transferMethod;
                    return [4, setAuthorizationHeaderIfRequired(configWithAntiCsrf)];
                  case 8:
                    _d.sent();
                    _d.label = 9;
                  case 9:
                    _d.trys.push([9, 14, , 27]);
                    localPrevError = prevError;
                    localPrevResponse = prevResponse;
                    prevError = void 0;
                    prevResponse = void 0;
                    if (localPrevError !== void 0) {
                      (0, logger_1.logDebugMessage)(
                        "doRequest: Not making call because localPrevError is not undefined"
                      );
                      throw localPrevError;
                    }
                    if (localPrevResponse !== void 0) {
                      (0, logger_1.logDebugMessage)(
                        "doRequest: Not making call because localPrevResponse is not undefined"
                      );
                    } else {
                      (0, logger_1.logDebugMessage)("doRequest: Making user's http call");
                    }
                    if (!(localPrevResponse === void 0)) return [3, 11];
                    return [4, httpCall(configWithAntiCsrf)];
                  case 10:
                    _b = _d.sent();
                    return [3, 12];
                  case 11:
                    _b = localPrevResponse;
                    _d.label = 12;
                  case 12:
                    response = _b;
                    (0, logger_1.logDebugMessage)("doRequest: User's http call ended");
                    return [4, saveTokensFromHeaders(response)];
                  case 13:
                    _d.sent();
                    (0, fetch_1.fireSessionUpdateEventsIfNecessary)(preRequestLSS.status === "EXISTS", response.status, response.headers["front-token"]);
                    return [2, response];
                  case 14:
                    err_1 = _d.sent();
                    response = err_1.response;
                    if (!(response !== void 0)) return [3, 25];
                    return [4, saveTokensFromHeaders(response)];
                  case 15:
                    _d.sent();
                    (0, fetch_1.fireSessionUpdateEventsIfNecessary)(preRequestLSS.status === "EXISTS", response.status, response.headers["front-token"]);
                    if (!(response.status === fetch_1.default.config.sessionExpiredStatusCode))
                      return [3, 21];
                    (0, logger_1.logDebugMessage)("doRequest: Status code is: " + response.status);
                    if (hasExceededMaxSessionRefreshAttempts(config)) {
                      (0, logger_1.logDebugMessage)(
                        "doRequest: Maximum session refresh attempts reached. sessionRefreshAttempts: ".concat(
                          config.__supertokensSessionRefreshAttempts,
                          ", maxRetryAttemptsForSessionRefresh: "
                        ).concat(fetch_1.default.config.maxRetryAttemptsForSessionRefresh)
                      );
                      errorMessage = "Received a 401 response from ".concat(
                        url,
                        ". Attempted to refresh the session and retry the request with the updated session tokens "
                      ).concat(
                        fetch_1.default.config.maxRetryAttemptsForSessionRefresh,
                        " times, but each attempt resulted in a 401 error. The maximum session refresh limit has been reached. Please investigate your API. To increase the session refresh attempts, update maxRetryAttemptsForSessionRefresh in the config."
                      );
                      console.error(errorMessage);
                      throw new Error(errorMessage);
                    }
                    return [4, (0, fetch_1.onUnauthorisedResponse)(preRequestLSS)];
                  case 16:
                    refreshResult = _d.sent();
                    incrementSessionRefreshAttemptCount(config);
                    (0, logger_1.logDebugMessage)("doRequest: sessionRefreshAttempts: " + config.__supertokensSessionRefreshAttempts);
                    if (!(refreshResult.result !== "RETRY")) return [3, 20];
                    (0, logger_1.logDebugMessage)("doRequest: Not retrying original request");
                    if (!(refreshResult.error !== void 0)) return [3, 18];
                    return [4, (0, axiosError_1.createAxiosErrorFromFetchResp)(refreshResult.error)];
                  case 17:
                    _c = _d.sent();
                    return [3, 19];
                  case 18:
                    _c = err_1;
                    _d.label = 19;
                  case 19:
                    returnObj = _c;
                    return [3, 28];
                  case 20:
                    (0, logger_1.logDebugMessage)("doRequest: Retrying original request");
                    return [3, 24];
                  case 21:
                    if (!(response.status === fetch_1.default.config.invalidClaimStatusCode))
                      return [3, 23];
                    return [4, (0, fetch_1.onInvalidClaimResponse)(response)];
                  case 22:
                    _d.sent();
                    _d.label = 23;
                  case 23:
                    throw err_1;
                  case 24:
                    return [3, 26];
                  case 25:
                    throw err_1;
                  case 26:
                    return [3, 27];
                  case 27:
                    return [3, 4];
                  case 28:
                    throw returnObj;
                }
              });
            });
          };
          return AuthHttpRequest2;
        })()
      );
      exports.default = AuthHttpRequest;
      function setAuthorizationHeaderIfRequired(requestConfig) {
        return __awaiter(this, void 0, void 0, function() {
          var accessToken, refreshToken;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                if (requestConfig.headers === void 0) {
                  requestConfig.headers = {};
                }
                (0, logger_1.logDebugMessage)("setAuthorizationHeaderIfRequired: adding existing tokens as header");
                return [4, (0, fetch_1.getTokenForHeaderAuth)("access")];
              case 1:
                accessToken = _b.sent();
                return [4, (0, fetch_1.getTokenForHeaderAuth)("refresh")];
              case 2:
                refreshToken = _b.sent();
                if (accessToken !== void 0 && refreshToken !== void 0) {
                  if (requestConfig.headers["Authorization"] !== void 0 || requestConfig.headers["authorization"] !== void 0) {
                    (0, logger_1.logDebugMessage)(
                      "setAuthorizationHeaderIfRequired: Authorization header defined by the user, not adding"
                    );
                  } else {
                    (0, logger_1.logDebugMessage)(
                      "setAuthorizationHeaderIfRequired: added authorization header"
                    );
                    requestConfig.headers = __assign(__assign({}, requestConfig.headers), {
                      Authorization: "Bearer ".concat(accessToken)
                    });
                    requestConfig.__supertokensAddedAuthHeader = true;
                  }
                } else {
                  (0, logger_1.logDebugMessage)(
                    "setAuthorizationHeaderIfRequired: token for header based auth not found"
                  );
                }
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      function saveTokensFromHeaders(response) {
        return __awaiter(this, void 0, void 0, function() {
          var refreshToken, accessToken, frontToken, responseHeaders_1, antiCsrfToken, tok;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: Saving updated tokens from the response");
                refreshToken = response.headers["st-refresh-token"];
                if (!(refreshToken !== void 0)) return [3, 2];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: saving new refresh token");
                return [4, (0, fetch_1.setToken)("refresh", refreshToken)];
              case 1:
                _b.sent();
                _b.label = 2;
              case 2:
                accessToken = response.headers["st-access-token"];
                if (!(accessToken !== void 0)) return [3, 4];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: saving new access token");
                return [4, (0, fetch_1.setToken)("access", accessToken)];
              case 3:
                _b.sent();
                _b.label = 4;
              case 4:
                frontToken = response.headers["front-token"];
                if (!(frontToken !== void 0)) return [3, 6];
                (0, logger_1.logDebugMessage)("doRequest: Setting sFrontToken: " + frontToken);
                return [4, fetch_1.FrontToken.setItem(frontToken)];
              case 5:
                _b.sent();
                responseHeaders_1 = new Headers();
                Object.entries(response.headers).forEach(function(_b2) {
                  var key = _b2[0], value = _b2[1];
                  Array.isArray(value) ? value.forEach(function(item) {
                    return responseHeaders_1.append(key, item);
                  }) : responseHeaders_1.append(key, value);
                });
                (0, fetch_1.updateClockSkewUsingFrontToken)({ frontToken, responseHeaders: responseHeaders_1 });
                _b.label = 6;
              case 6:
                antiCsrfToken = response.headers["anti-csrf"];
                if (!(antiCsrfToken !== void 0)) return [3, 9];
                return [4, (0, fetch_1.getLocalSessionState)(false)];
              case 7:
                tok = _b.sent();
                if (!(tok.status === "EXISTS")) return [3, 9];
                (0, logger_1.logDebugMessage)("doRequest: Setting anti-csrf token");
                return [4, fetch_1.AntiCsrfToken.setItem(tok.lastAccessTokenUpdate, antiCsrfToken)];
              case 8:
                _b.sent();
                _b.label = 9;
              case 9:
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      function removeAuthHeaderIfMatchesLocalToken(config) {
        return __awaiter(this, void 0, void 0, function() {
          var accessToken, refreshToken, authHeader, res;
          return __generator(this, function(_b) {
            switch (_b.label) {
              case 0:
                return [4, (0, fetch_1.getTokenForHeaderAuth)("access")];
              case 1:
                accessToken = _b.sent();
                return [4, (0, fetch_1.getTokenForHeaderAuth)("refresh")];
              case 2:
                refreshToken = _b.sent();
                authHeader = config.headers.Authorization || config.headers.authorization;
                if (accessToken !== void 0 && refreshToken !== void 0) {
                  if (authHeader === "Bearer ".concat(accessToken) || "__supertokensAddedAuthHeader" in config) {
                    (0, logger_1.logDebugMessage)(
                      "removeAuthHeaderIfMatchesLocalToken: Removing Authorization from user provided headers because it contains our access token"
                    );
                    res = __assign(__assign({}, config), { headers: __assign({}, config.headers) });
                    delete res.headers.authorization;
                    delete res.headers.Authorization;
                    return [2, res];
                  }
                }
                return [2, config];
            }
          });
        });
      }
    }
  });

  // node_modules/supertokens-website/lib/build/error.js
  var require_error = __commonJS({
    "node_modules/supertokens-website/lib/build/error.js"(exports) {
      "use strict";
      var __extends = exports && exports.__extends || /* @__PURE__ */ (function() {
        var extendStatics = function(d, b) {
          extendStatics = Object.setPrototypeOf || { __proto__: [] } instanceof Array && function(d2, b2) {
            d2.__proto__ = b2;
          } || function(d2, b2) {
            for (var p in b2) if (Object.prototype.hasOwnProperty.call(b2, p)) d2[p] = b2[p];
          };
          return extendStatics(d, b);
        };
        return function(d, b) {
          if (typeof b !== "function" && b !== null)
            throw new TypeError("Class extends value " + String(b) + " is not a constructor or null");
          extendStatics(d, b);
          function __() {
            this.constructor = d;
          }
          d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
        };
      })();
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.STGeneralError = void 0;
      var STGeneralError = (
        /** @class */
        (function(_super) {
          __extends(STGeneralError2, _super);
          function STGeneralError2(message) {
            var _this = _super.call(this, message) || this;
            _this.isSuperTokensGeneralError = true;
            return _this;
          }
          STGeneralError2.isThisError = function(err) {
            return err.isSuperTokensGeneralError === true;
          };
          return STGeneralError2;
        })(Error)
      );
      exports.STGeneralError = STGeneralError;
    }
  });

  // node_modules/supertokens-website/lib/build/xmlhttprequest.js
  var require_xmlhttprequest = __commonJS({
    "node_modules/supertokens-website/lib/build/xmlhttprequest.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.addInterceptorsToXMLHttpRequest = void 0;
      var fetch_1 = require_fetch();
      var logger_1 = require_logger();
      var windowHandler_1 = require_windowHandler();
      var processState_1 = require_processState();
      var XHR_EVENTS = ["readystatechange", "abort", "error", "load", "loadend", "loadstart", "progress", "timeout"];
      function addInterceptorsToXMLHttpRequest() {
        var firstEventLoopDone = new Promise(function(res) {
          return setTimeout(res, 0);
        });
        var oldXMLHttpRequest = XMLHttpRequest;
        (0, logger_1.logDebugMessage)("addInterceptorsToXMLHttpRequest called");
        XMLHttpRequest = function() {
          var actual = new oldXMLHttpRequest();
          var self = this;
          var listOfFunctionCallsInProxy = [];
          var requestHeaders = [];
          var customGetterValues = {};
          var customResponseHeaders;
          var eventHandlers = /* @__PURE__ */ new Map();
          var delayedQueue = firstEventLoopDone;
          function delayIfNecessary(cb) {
            delayedQueue = delayedQueue.finally(function() {
              var _a;
              return (_a = cb()) === null || _a === void 0 ? void 0 : _a.catch(function(err) {
                var ev = new ProgressEvent("error");
                ev.error = err;
                if (self.onerror !== void 0 && self.onerror !== null) {
                  self.onerror(ev);
                }
                redispatchEvent("error", ev);
              });
            });
          }
          var url = "";
          var doNotDoInterception = false;
          var preRequestLSS = void 0;
          var body;
          var sessionRefreshAttempts = 0;
          self.onload = null;
          self.onreadystatechange = null;
          self.onloadend = null;
          self.addEventListener = function(type, listener, _options) {
            var handlers = eventHandlers.get(type);
            if (handlers === void 0) {
              handlers = /* @__PURE__ */ new Set();
              eventHandlers.set(type, handlers);
            }
            handlers.add(listener);
          };
          self.removeEventListener = function(type, listener) {
            var handlers = eventHandlers.get(type);
            if (handlers === void 0) {
              handlers = /* @__PURE__ */ new Set();
              eventHandlers.set(type, handlers);
            }
            handlers.delete(listener);
          };
          function redispatchEvent(name, ev) {
            var handlers = eventHandlers.get(name);
            (0, logger_1.logDebugMessage)(
              "XHRInterceptor dispatching ".concat(ev.type, " to ").concat(handlers ? handlers.size : 0, " listeners")
            );
            if (handlers) {
              Array.from(handlers).forEach(function(handler) {
                return handler.apply(self, [ev]);
              });
            }
          }
          function handleRetryPostRefreshing() {
            return __awaiter(this, void 0, void 0, function() {
              var errorMessage, refreshResult, retryXhr;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    if (preRequestLSS === void 0) {
                      throw new Error("Should never come here..");
                    }
                    (0, logger_1.logDebugMessage)("XHRInterceptor.handleRetryPostRefreshing: preRequestLSS " + preRequestLSS.status);
                    if (sessionRefreshAttempts >= fetch_1.default.config.maxRetryAttemptsForSessionRefresh) {
                      (0, logger_1.logDebugMessage)(
                        "XHRInterceptor.handleRetryPostRefreshing: Maximum session refresh attempts reached. sessionRefreshAttempts: ".concat(sessionRefreshAttempts, ", maxRetryAttemptsForSessionRefresh: ").concat(fetch_1.default.config.maxRetryAttemptsForSessionRefresh)
                      );
                      customGetterValues["status"] = 0;
                      customGetterValues["statusText"] = "";
                      customGetterValues["responseType"] = "";
                      errorMessage = "Received a 401 response from ".concat(
                        url,
                        ". Attempted to refresh the session and retry the request with the updated session tokens "
                      ).concat(
                        fetch_1.default.config.maxRetryAttemptsForSessionRefresh,
                        " times, but each attempt resulted in a 401 error. The maximum session refresh limit has been reached. Please investigate your API. To increase the session refresh attempts, update maxRetryAttemptsForSessionRefresh in the config."
                      );
                      console.error(errorMessage);
                      throw new Error(errorMessage);
                    }
                    return [4, (0, fetch_1.onUnauthorisedResponse)(preRequestLSS)];
                  case 1:
                    refreshResult = _a.sent();
                    sessionRefreshAttempts++;
                    (0, logger_1.logDebugMessage)("XHRInterceptor.handleRetryPostRefreshing: sessionRefreshAttempts: " + sessionRefreshAttempts);
                    if (refreshResult.result !== "RETRY") {
                      (0, logger_1.logDebugMessage)(
                        "XHRInterceptor.handleRetryPostRefreshing: Not retrying original request " + !!refreshResult.error
                      );
                      if (refreshResult.error !== void 0) {
                        throw refreshResult.error;
                      }
                      return [2, true];
                    }
                    (0, logger_1.logDebugMessage)("XHRInterceptor.handleRetryPostRefreshing: Retrying original request");
                    retryXhr = new oldXMLHttpRequest();
                    setUpXHR(self, retryXhr, true);
                    listOfFunctionCallsInProxy.forEach(function(i) {
                      i(retryXhr);
                    });
                    sendXHR(retryXhr, body);
                    return [2, false];
                }
              });
            });
          }
          function handleResponse(xhr) {
            return __awaiter(this, void 0, void 0, function() {
              var status_1, headers, err_1, resp, ev;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    if (doNotDoInterception) {
                      (0, logger_1.logDebugMessage)(
                        "XHRInterceptor.handleResponse: Returning without interception"
                      );
                      return [2, true];
                    }
                    _a.label = 1;
                  case 1:
                    _a.trys.push([1, 7, , 11]);
                    (0, logger_1.logDebugMessage)("XHRInterceptor.handleResponse: Interception started");
                    processState_1.ProcessState.getInstance().addState(
                      processState_1.PROCESS_STATE.CALLING_INTERCEPTION_RESPONSE
                    );
                    status_1 = xhr.status;
                    headers = getResponseHeadersFromXHR(xhr);
                    return [4, saveTokensFromHeaders(headers)];
                  case 2:
                    _a.sent();
                    (0, fetch_1.fireSessionUpdateEventsIfNecessary)(preRequestLSS.status === "EXISTS", status_1, headers.get("front-token"));
                    if (!(status_1 === fetch_1.default.config.sessionExpiredStatusCode))
                      return [3, 4];
                    (0, logger_1.logDebugMessage)("responseInterceptor: Status code is: " + status_1);
                    return [4, handleRetryPostRefreshing()];
                  case 3:
                    return [2, _a.sent()];
                  case 4:
                    if (!(status_1 === fetch_1.default.config.invalidClaimStatusCode)) return [3, 6];
                    return [4, (0, fetch_1.onInvalidClaimResponse)({ data: xhr.responseText })];
                  case 5:
                    _a.sent();
                    _a.label = 6;
                  case 6:
                    return [2, true];
                  case 7:
                    err_1 = _a.sent();
                    (0, logger_1.logDebugMessage)("XHRInterceptor.handleResponse: caught error");
                    if (!(err_1.status !== void 0)) return [3, 9];
                    return [4, getXMLHttpStatusAndResponseTextFromFetchResponse(err_1)];
                  case 8:
                    resp = _a.sent();
                    customGetterValues["status"] = resp.status;
                    customGetterValues["statusText"] = resp.statusText;
                    customGetterValues["responseType"] = resp.responseType;
                    customResponseHeaders = resp.headers;
                    if (resp.responseType === "json") {
                      try {
                        customGetterValues["response"] = JSON.parse(resp.responseText);
                      } catch (_b) {
                        customGetterValues["response"] = resp.responseText;
                      }
                    } else {
                      customGetterValues["response"] = resp.responseText;
                    }
                    customGetterValues["responseText"] = resp.responseText;
                    return [3, 10];
                  case 9:
                    ev = new ProgressEvent("error");
                    ev.error = err_1;
                    if (self.onerror !== void 0 && self.onerror !== null) {
                      self.onerror(ev);
                    }
                    redispatchEvent("error", ev);
                    _a.label = 10;
                  case 10:
                    return [2, true];
                  case 11:
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          }
          self.open = function(_, u) {
            (0, logger_1.logDebugMessage)("XHRInterceptor.open called");
            var args = arguments;
            url = u;
            try {
              doNotDoInterception = typeof url === "string" && !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                url,
                fetch_1.default.config.apiDomain,
                fetch_1.default.config.sessionTokenBackendDomain
              ) || typeof url !== "string" && !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                url.toString(),
                fetch_1.default.config.apiDomain,
                fetch_1.default.config.sessionTokenBackendDomain
              );
            } catch (err) {
              if (err.message === "Please provide a valid domain name") {
                (0, logger_1.logDebugMessage)(
                  "XHRInterceptor.open: Trying shouldDoInterceptionBasedOnUrl with location.origin"
                );
                doNotDoInterception = !fetch_1.default.recipeImpl.shouldDoInterceptionBasedOnUrl(
                  windowHandler_1.default.getReferenceOrThrow().windowHandler.location.getOrigin(),
                  fetch_1.default.config.apiDomain,
                  fetch_1.default.config.sessionTokenBackendDomain
                );
              } else {
                throw err;
              }
            }
            delayIfNecessary(function() {
              listOfFunctionCallsInProxy.push(function(xhr) {
                xhr.open.apply(xhr, args);
              });
              actual.open.apply(actual, args);
            });
          };
          self.send = function(inputBody) {
            body = inputBody;
            sendXHR(actual, body);
          };
          self.setRequestHeader = function(name, value) {
            var _this = this;
            (0, logger_1.logDebugMessage)("XHRInterceptor.setRequestHeader: Called with ".concat(name));
            if (doNotDoInterception) {
              delayIfNecessary(function() {
                return actual.setRequestHeader(name, value);
              });
              return;
            }
            if (name === "anti-csrf") {
              return;
            }
            delayIfNecessary(function() {
              return __awaiter(_this, void 0, void 0, function() {
                var accessToken, refreshToken;
                return __generator(this, function(_a) {
                  switch (_a.label) {
                    case 0:
                      if (!(name.toLowerCase() === "authorization")) return [3, 3];
                      (0, logger_1.logDebugMessage)("XHRInterceptor.setRequestHeader: checking if user provided auth header matches local token");
                      return [4, (0, fetch_1.getTokenForHeaderAuth)("access")];
                    case 1:
                      accessToken = _a.sent();
                      return [4, (0, fetch_1.getTokenForHeaderAuth)("refresh")];
                    case 2:
                      refreshToken = _a.sent();
                      if (accessToken !== void 0 && refreshToken !== void 0 && value === "Bearer ".concat(accessToken)) {
                        (0, logger_1.logDebugMessage)(
                          "XHRInterceptor.setRequestHeader: skipping Authorization from user provided headers because it contains our access token"
                        );
                        return [
                          2
                          /*return*/
                        ];
                      }
                      _a.label = 3;
                    case 3:
                      listOfFunctionCallsInProxy.push(function(xhr) {
                        xhr.setRequestHeader(name, value);
                      });
                      requestHeaders.push({ name, value });
                      actual.setRequestHeader(name, value);
                      return [
                        2
                        /*return*/
                      ];
                  }
                });
              });
            });
          };
          var copiedProps = void 0;
          setUpXHR(self, actual, false);
          function setUpXHR(self2, xhr, isRetry) {
            var responseProcessed;
            var delayedEvents = ["load", "loadend", "readystatechange"];
            (0, logger_1.logDebugMessage)("XHRInterceptor.setUpXHR called");
            var _loop_1 = function(name_12) {
              (0, logger_1.logDebugMessage)("XHRInterceptor added listener for event ".concat(name_12));
              xhr.addEventListener(name_12, function(ev) {
                (0, logger_1.logDebugMessage)("XHRInterceptor got event ".concat(name_12));
                if (!delayedEvents.includes(name_12)) {
                  redispatchEvent(name_12, ev);
                }
              });
            };
            for (var _i = 0, XHR_EVENTS_1 = XHR_EVENTS; _i < XHR_EVENTS_1.length; _i++) {
              var name_1 = XHR_EVENTS_1[_i];
              _loop_1(name_1);
            }
            xhr.onload = function(ev) {
              if (responseProcessed === void 0) {
                responseProcessed = handleResponse(xhr);
              }
              responseProcessed.then(function(callself) {
                if (!callself) {
                  return;
                }
                if (self2.onload) {
                  self2.onload(ev);
                }
                redispatchEvent("load", ev);
              });
            };
            xhr.onreadystatechange = function(ev) {
              if (xhr.readyState === oldXMLHttpRequest.DONE) {
                if (responseProcessed === void 0) {
                  responseProcessed = handleResponse(xhr);
                }
                responseProcessed.then(function(callself) {
                  if (!callself) {
                    return;
                  }
                  if (self2.onreadystatechange) self2.onreadystatechange(ev);
                  redispatchEvent("readystatechange", ev);
                });
              } else {
                if (self2.onreadystatechange) {
                  self2.onreadystatechange(ev);
                }
                redispatchEvent("readystatechange", ev);
              }
            };
            xhr.onloadend = function(ev) {
              if (responseProcessed === void 0) {
                responseProcessed = handleResponse(xhr);
              }
              responseProcessed.then(function(callself) {
                if (!callself) {
                  return;
                }
                if (self2.onloadend) {
                  self2.onloadend(ev);
                }
                redispatchEvent("loadend", ev);
              });
            };
            self2.getAllResponseHeaders = function() {
              var headersString;
              if (customResponseHeaders) {
                headersString = "";
                customResponseHeaders.forEach(function(v, k) {
                  return headersString += "".concat(k, ": ").concat(v, "\r\n");
                });
              } else {
                headersString = xhr.getAllResponseHeaders();
              }
              return headersString + "x-supertokens-xhr-intercepted: true\r\n";
            };
            self2.getResponseHeader = function(name) {
              if (name === "x-supertokens-xhr-intercepted") {
                return "true";
              }
              if (customResponseHeaders) {
                return customResponseHeaders.get(name);
              }
              return xhr.getResponseHeader(name);
            };
            if (copiedProps === void 0) {
              copiedProps = [];
              for (var prop in xhr) {
                if (!(prop in self2)) {
                  copiedProps.push(prop);
                }
              }
            }
            var _loop_2 = function(prop2) {
              if (typeof xhr[prop2] === "function") {
                Object.defineProperty(self2, prop2, {
                  configurable: true,
                  value: function() {
                    var args = arguments;
                    if (!isRetry) {
                      listOfFunctionCallsInProxy.push(function(xhr2) {
                        xhr2[prop2].apply(xhr2, args);
                      });
                    }
                    return xhr[prop2].apply(xhr, args);
                  }
                });
              } else {
                Object.defineProperty(self2, prop2, {
                  configurable: true,
                  get: function() {
                    if (customGetterValues[prop2] !== void 0) {
                      return customGetterValues[prop2];
                    }
                    return xhr[prop2];
                  },
                  set: function(val) {
                    if (!isRetry) {
                      listOfFunctionCallsInProxy.push(function(xhr2) {
                        xhr2[prop2] = val;
                      });
                    }
                    (0, logger_1.logDebugMessage)("XHRInterceptor.set[".concat(prop2, "] = ").concat(val));
                    xhr[prop2] = val;
                  }
                });
              }
            };
            for (var _a = 0, copiedProps_1 = copiedProps; _a < copiedProps_1.length; _a++) {
              var prop = copiedProps_1[_a];
              _loop_2(prop);
            }
          }
          function sendXHR(xhr, body2) {
            var _this = this;
            (0, logger_1.logDebugMessage)("XHRInterceptor.send: called");
            (0, logger_1.logDebugMessage)("XHRInterceptor.send: Value of doNotDoInterception: " + doNotDoInterception);
            if (doNotDoInterception) {
              (0, logger_1.logDebugMessage)("XHRInterceptor.send: Returning without interception");
              delayIfNecessary(function() {
                return xhr.send(body2);
              });
              return;
            }
            (0, logger_1.logDebugMessage)("XHRInterceptor.send: Interception started");
            processState_1.ProcessState.getInstance().addState(
              processState_1.PROCESS_STATE.CALLING_INTERCEPTION_REQUEST
            );
            delayIfNecessary(function() {
              return __awaiter(_this, void 0, void 0, function() {
                var antiCsrfToken, transferMethod;
                return __generator(this, function(_a) {
                  switch (_a.label) {
                    case 0:
                      return [4, (0, fetch_1.getLocalSessionState)(true)];
                    case 1:
                      preRequestLSS = _a.sent();
                      if (!(preRequestLSS.status === "EXISTS")) return [3, 3];
                      return [
                        4,
                        fetch_1.AntiCsrfToken.getToken(preRequestLSS.lastAccessTokenUpdate)
                      ];
                    case 2:
                      antiCsrfToken = _a.sent();
                      if (antiCsrfToken !== void 0) {
                        (0, logger_1.logDebugMessage)(
                          "XHRInterceptor.send: Adding anti-csrf token to request"
                        );
                        xhr.setRequestHeader("anti-csrf", antiCsrfToken);
                      }
                      _a.label = 3;
                    case 3:
                      if (fetch_1.default.config.autoAddCredentials) {
                        (0, logger_1.logDebugMessage)("XHRInterceptor.send: Adding credentials include");
                        self.withCredentials = true;
                      }
                      if (!requestHeaders.some(function(i) {
                        return i.name === "rid";
                      })) {
                        (0, logger_1.logDebugMessage)("XHRInterceptor.send: Adding rid header: anti-csrf");
                        xhr.setRequestHeader("rid", "anti-csrf");
                      } else {
                        (0, logger_1.logDebugMessage)(
                          "XHRInterceptor.send: rid header was already there in request"
                        );
                      }
                      transferMethod = fetch_1.default.config.tokenTransferMethod;
                      if (!requestHeaders.some(function(i) {
                        return i.name === "st-auth-mode";
                      })) {
                        (0, logger_1.logDebugMessage)(
                          "XHRInterceptor.send: Adding st-auth-mode header: " + transferMethod
                        );
                        xhr.setRequestHeader("st-auth-mode", transferMethod);
                      } else {
                        (0, logger_1.logDebugMessage)(
                          "XHRInterceptor.send: st-auth-mode header was already there in request"
                        );
                      }
                      return [4, setAuthorizationHeaderIfRequired(xhr, requestHeaders)];
                    case 4:
                      _a.sent();
                      (0, logger_1.logDebugMessage)("XHRInterceptor.send: Making user's http call");
                      return [2, xhr.send(body2)];
                  }
                });
              });
            });
          }
        };
        XMLHttpRequest.__interceptedBySuperTokens = true;
        XMLHttpRequest.__original = oldXMLHttpRequest;
      }
      exports.addInterceptorsToXMLHttpRequest = addInterceptorsToXMLHttpRequest;
      function getXMLHttpStatusAndResponseTextFromFetchResponse(response) {
        return __awaiter(this, void 0, void 0, function() {
          var contentType, data, responseType, _a, _b, _c;
          return __generator(this, function(_d) {
            switch (_d.label) {
              case 0:
                contentType = response.headers.get("content-type");
                data = "";
                responseType = "text";
                if (!(contentType === null)) return [3, 5];
                _d.label = 1;
              case 1:
                _d.trys.push([1, 3, , 4]);
                return [4, response.text()];
              case 2:
                data = _d.sent();
                return [3, 4];
              case 3:
                _a = _d.sent();
                data = "";
                return [3, 4];
              case 4:
                return [3, 9];
              case 5:
                if (!contentType.includes("application/json")) return [3, 7];
                responseType = "json";
                _c = (_b = JSON).stringify;
                return [4, response.json()];
              case 6:
                data = _c.apply(_b, [_d.sent()]);
                return [3, 9];
              case 7:
                if (!contentType.includes("text/")) return [3, 9];
                return [4, response.text()];
              case 8:
                data = _d.sent();
                _d.label = 9;
              case 9:
                return [
                  2,
                  {
                    status: response.status,
                    responseText: data,
                    statusText: response.statusText,
                    responseType,
                    headers: response.headers
                  }
                ];
            }
          });
        });
      }
      function setAuthorizationHeaderIfRequired(xhr, requestHeaders) {
        return __awaiter(this, void 0, void 0, function() {
          var accessToken, refreshToken;
          return __generator(this, function(_a) {
            switch (_a.label) {
              case 0:
                (0, logger_1.logDebugMessage)("setAuthorizationHeaderIfRequired: adding existing tokens as header");
                return [4, (0, fetch_1.getTokenForHeaderAuth)("access")];
              case 1:
                accessToken = _a.sent();
                return [4, (0, fetch_1.getTokenForHeaderAuth)("refresh")];
              case 2:
                refreshToken = _a.sent();
                if (accessToken !== void 0 && refreshToken !== void 0) {
                  if (requestHeaders.some(function(_a2) {
                    var name = _a2.name;
                    return name.toLowerCase() === "authorization";
                  })) {
                    (0, logger_1.logDebugMessage)(
                      "setAuthorizationHeaderIfRequired: Authorization header defined by the user, not adding"
                    );
                  } else {
                    if (accessToken !== void 0) {
                      (0, logger_1.logDebugMessage)(
                        "setAuthorizationHeaderIfRequired: added authorization header"
                      );
                      xhr.setRequestHeader("Authorization", "Bearer ".concat(accessToken));
                    }
                  }
                } else {
                  (0, logger_1.logDebugMessage)(
                    "setAuthorizationHeaderIfRequired: token for header based auth not found"
                  );
                }
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      function saveTokensFromHeaders(headers) {
        return __awaiter(this, void 0, void 0, function() {
          var refreshToken, accessToken, frontToken, antiCsrfToken, tok;
          return __generator(this, function(_a) {
            switch (_a.label) {
              case 0:
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: Saving updated tokens from the response");
                refreshToken = headers.get("st-refresh-token");
                if (!(refreshToken !== null)) return [3, 2];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: saving new refresh token");
                return [4, (0, fetch_1.setToken)("refresh", refreshToken)];
              case 1:
                _a.sent();
                _a.label = 2;
              case 2:
                accessToken = headers.get("st-access-token");
                if (!(accessToken !== null)) return [3, 4];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: saving new access token");
                return [4, (0, fetch_1.setToken)("access", accessToken)];
              case 3:
                _a.sent();
                _a.label = 4;
              case 4:
                frontToken = headers.get("front-token");
                if (!(frontToken !== null)) return [3, 6];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: Setting sFrontToken: " + frontToken);
                return [4, fetch_1.FrontToken.setItem(frontToken)];
              case 5:
                _a.sent();
                (0, fetch_1.updateClockSkewUsingFrontToken)({ frontToken, responseHeaders: headers });
                _a.label = 6;
              case 6:
                antiCsrfToken = headers.get("anti-csrf");
                if (!(antiCsrfToken !== null)) return [3, 9];
                return [4, (0, fetch_1.getLocalSessionState)(false)];
              case 7:
                tok = _a.sent();
                if (!(tok.status === "EXISTS")) return [3, 9];
                (0, logger_1.logDebugMessage)("saveTokensFromHeaders: Setting anti-csrf token");
                return [4, fetch_1.AntiCsrfToken.setItem(tok.lastAccessTokenUpdate, antiCsrfToken)];
              case 8:
                _a.sent();
                _a.label = 9;
              case 9:
                return [
                  2
                  /*return*/
                ];
            }
          });
        });
      }
      function getResponseHeadersFromXHR(xhr) {
        return new Headers(
          xhr.getAllResponseHeaders().split("\r\n").map(function(line) {
            var sep = line.indexOf(": ");
            if (sep === -1) {
              return ["", ""];
            }
            return [line.slice(0, sep), line.slice(sep + 2)];
          }).filter(function(e) {
            return e[0].length !== 0;
          })
        );
      }
    }
  });

  // node_modules/supertokens-website/lib/build/recipeImplementation.js
  var require_recipeImplementation = __commonJS({
    "node_modules/supertokens-website/lib/build/recipeImplementation.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      var fetch_1 = require_fetch();
      var axios_1 = require_axios();
      var version_1 = require_version();
      var logger_1 = require_logger();
      var error_1 = require_error();
      var xmlhttprequest_1 = require_xmlhttprequest();
      var utils_1 = require_utils();
      var dateProvider_1 = require_dateProvider();
      var lockFactory_1 = require_lockFactory();
      var MAX_REFRESH_LOCK_TRY_COUNT = 100;
      var CLAIM_REFRESH_LOCK_NAME = "CLAIM_REFRESH_LOCK";
      function RecipeImplementation(recipeImplInput) {
        return {
          addXMLHttpRequestInterceptor: function(_) {
            (0, logger_1.logDebugMessage)("addXMLHttpRequestInterceptorAndReturnModified: called");
            (0, xmlhttprequest_1.addInterceptorsToXMLHttpRequest)();
          },
          addFetchInterceptorsAndReturnModifiedFetch: function(input) {
            (0, logger_1.logDebugMessage)("addFetchInterceptorsAndReturnModifiedFetch: called");
            return function(url, config) {
              return __awaiter(this, void 0, void 0, function() {
                return __generator(this, function(_a) {
                  switch (_a.label) {
                    case 0:
                      return [
                        4,
                        fetch_1.default.doRequest(
                          function(config2) {
                            return input.originalFetch(
                              typeof url === "object" && "clone" in url ? url.clone() : url,
                              __assign({}, config2)
                            );
                          },
                          config,
                          url
                        )
                      ];
                    case 1:
                      return [2, _a.sent()];
                  }
                });
              });
            };
          },
          addAxiosInterceptors: function(input) {
            (0, logger_1.logDebugMessage)("addAxiosInterceptors: called");
            if (XMLHttpRequest.__interceptedBySuperTokens) {
              console.warn(
                "Not adding axios interceptor since XMLHttpRequest is already added. This is just a warning."
              );
              console.warn("Our axios and XMLHttpRequest interceptors cannot be used at the same time.");
              console.warn(
                "Since XMLHttpRequest is added automatically and supports axios by default, you can just remove addAxiosInterceptors from your code."
              );
              console.warn(
                "If you want to continue using our axios interceptor, you can override addXMLHttpRequestInterceptor with an empty function."
              );
              (0, logger_1.logDebugMessage)(
                "addAxiosInterceptors: not adding, because XHR interceptors are already in place"
              );
              return;
            }
            var requestInterceptors = input.axiosInstance.interceptors.request;
            for (var i = 0; i < requestInterceptors.handlers.length; i++) {
              if (requestInterceptors.handlers[i].fulfilled === axios_1.interceptorFunctionRequestFulfilled) {
                (0, logger_1.logDebugMessage)(
                  "addAxiosInterceptors: not adding because already added on this instance"
                );
                return;
              }
            }
            input.axiosInstance.interceptors.request.use(axios_1.interceptorFunctionRequestFulfilled, function(error) {
              return __awaiter(this, void 0, void 0, function() {
                return __generator(this, function(_a) {
                  throw error;
                });
              });
            });
            input.axiosInstance.interceptors.response.use(
              (0, axios_1.responseInterceptor)(input.axiosInstance),
              (0, axios_1.responseErrorInterceptor)(input.axiosInstance)
            );
          },
          getUserId: function(_) {
            return __awaiter(this, void 0, void 0, function() {
              var tokenInfo;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("getUserId: called");
                    return [4, fetch_1.FrontToken.getTokenInfo()];
                  case 1:
                    tokenInfo = _a.sent();
                    if (tokenInfo === void 0) {
                      throw new Error("No session exists");
                    }
                    (0, logger_1.logDebugMessage)("getUserId: returning: " + tokenInfo.uid);
                    return [2, tokenInfo.uid];
                }
              });
            });
          },
          getAccessTokenPayloadSecurely: function(input) {
            return __awaiter(this, void 0, void 0, function() {
              var tokenInfo, retry;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("getAccessTokenPayloadSecurely: called");
                    return [4, fetch_1.FrontToken.getTokenInfo()];
                  case 1:
                    tokenInfo = _a.sent();
                    if (tokenInfo === void 0) {
                      throw new Error("No session exists");
                    }
                    if (!(tokenInfo.ate < dateProvider_1.default.getReferenceOrThrow().dateProvider.now()))
                      return [3, 5];
                    (0, logger_1.logDebugMessage)("getAccessTokenPayloadSecurely: access token expired. Refreshing session");
                    return [4, fetch_1.default.attemptRefreshingSession()];
                  case 2:
                    retry = _a.sent();
                    if (!retry) return [3, 4];
                    return [
                      4,
                      this.getAccessTokenPayloadSecurely({
                        userContext: input.userContext
                      })
                    ];
                  case 3:
                    return [2, _a.sent()];
                  case 4:
                    throw new Error("Could not refresh session");
                  case 5:
                    (0, logger_1.logDebugMessage)("getAccessTokenPayloadSecurely: returning: " + JSON.stringify(tokenInfo.up));
                    return [2, tokenInfo.up];
                }
              });
            });
          },
          doesSessionExist: function(_) {
            return __awaiter(this, void 0, void 0, function() {
              var tokenInfo, preRequestLSS, refresh;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("doesSessionExist: called");
                    return [4, fetch_1.FrontToken.getTokenInfo()];
                  case 1:
                    tokenInfo = _a.sent();
                    if (tokenInfo === void 0) {
                      (0, logger_1.logDebugMessage)("doesSessionExist: access token does not exist locally");
                      return [2, false];
                    }
                    if (!(tokenInfo.ate < dateProvider_1.default.getReferenceOrThrow().dateProvider.now()))
                      return [3, 4];
                    (0, logger_1.logDebugMessage)("doesSessionExist: access token expired. Refreshing session");
                    return [4, (0, fetch_1.getLocalSessionState)(false)];
                  case 2:
                    preRequestLSS = _a.sent();
                    return [4, (0, fetch_1.onUnauthorisedResponse)(preRequestLSS)];
                  case 3:
                    refresh = _a.sent();
                    return [2, refresh.result === "RETRY"];
                  case 4:
                    return [2, true];
                }
              });
            });
          },
          signOut: function(input) {
            return __awaiter(this, void 0, void 0, function() {
              var preAPIResult, resp, responseJson, message;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    (0, logger_1.logDebugMessage)("signOut: called");
                    return [4, this.doesSessionExist(input)];
                  case 1:
                    if (!_a.sent()) {
                      (0, logger_1.logDebugMessage)("signOut: exiting early because session does not exist");
                      (0, logger_1.logDebugMessage)("signOut: firing SIGN_OUT event");
                      recipeImplInput.onHandleEvent({
                        action: "SIGN_OUT",
                        userContext: input.userContext
                      });
                      return [
                        2
                        /*return*/
                      ];
                    }
                    (0, logger_1.logDebugMessage)("signOut: Calling refresh pre API hook");
                    return [
                      4,
                      recipeImplInput.preAPIHook({
                        action: "SIGN_OUT",
                        requestInit: {
                          method: "post",
                          headers: {
                            "fdi-version": version_1.supported_fdi.join(","),
                            rid: fetch_1.default.rid
                          }
                        },
                        url: fetch_1.default.signOutUrl,
                        userContext: input.userContext
                      })
                    ];
                  case 2:
                    preAPIResult = _a.sent();
                    (0, logger_1.logDebugMessage)("signOut: Calling API");
                    return [4, fetch(preAPIResult.url, preAPIResult.requestInit)];
                  case 3:
                    resp = _a.sent();
                    (0, logger_1.logDebugMessage)("signOut: API ended");
                    (0, logger_1.logDebugMessage)("signOut: API responded with status code: " + resp.status);
                    if (resp.status === recipeImplInput.sessionExpiredStatusCode) {
                      return [
                        2
                        /*return*/
                      ];
                    }
                    if (resp.status >= 300) {
                      throw resp;
                    }
                    return [
                      4,
                      recipeImplInput.postAPIHook({
                        action: "SIGN_OUT",
                        requestInit: preAPIResult.requestInit,
                        url: preAPIResult.url,
                        fetchResponse: resp.clone(),
                        userContext: input.userContext
                      })
                    ];
                  case 4:
                    _a.sent();
                    return [4, resp.clone().json()];
                  case 5:
                    responseJson = _a.sent();
                    if (responseJson.status === "GENERAL_ERROR") {
                      (0, logger_1.logDebugMessage)("doRequest: Throwing general error");
                      message = responseJson.message === void 0 ? "No Error Message Provided" : responseJson.message;
                      throw new error_1.STGeneralError(message);
                    }
                    return [
                      2
                      /*return*/
                    ];
                }
              });
            });
          },
          getInvalidClaimsFromResponse: function(input) {
            return __awaiter(this, void 0, void 0, function() {
              var body;
              return __generator(this, function(_a) {
                switch (_a.label) {
                  case 0:
                    if (!("body" in input.response)) return [3, 2];
                    return [4, input.response.clone().json()];
                  case 1:
                    body = _a.sent();
                    return [3, 3];
                  case 2:
                    if (typeof input.response.data === "string") {
                      body = JSON.parse(input.response.data);
                    } else {
                      body = input.response.data;
                    }
                    _a.label = 3;
                  case 3:
                    return [2, body.claimValidationErrors];
                }
              });
            });
          },
          getGlobalClaimValidators: function(input) {
            return input.claimValidatorsAddedByOtherRecipes;
          },
          validateClaims: function(input) {
            return __awaiter(this, void 0, void 0, function() {
              var accessTokenPayload, tryCount, lockFactory, claimRefreshLock, _i, _a, validator, err_1, errors, _b, _c, validator, validationRes;
              return __generator(this, function(_d) {
                switch (_d.label) {
                  case 0:
                    tryCount = 0;
                    _d.label = 1;
                  case 1:
                    if (!(++tryCount < MAX_REFRESH_LOCK_TRY_COUNT)) return [3, 20];
                    return [4, lockFactory_1.default.getReferenceOrThrow().lockFactory()];
                  case 2:
                    lockFactory = _d.sent();
                    (0, logger_1.logDebugMessage)("validateClaims: trying to acquire claim refresh lock");
                    return [4, lockFactory.acquireLock(CLAIM_REFRESH_LOCK_NAME)];
                  case 3:
                    claimRefreshLock = _d.sent();
                    if (!claimRefreshLock) return [3, 18];
                    _d.label = 4;
                  case 4:
                    _d.trys.push([4, , 15, 17]);
                    return [
                      4,
                      this.getAccessTokenPayloadSecurely({
                        userContext: input.userContext
                      })
                    ];
                  case 5:
                    accessTokenPayload = _d.sent();
                    (0, logger_1.logDebugMessage)("validateClaims: claim refresh lock acquired");
                    _i = 0, _a = input.claimValidators;
                    _d.label = 6;
                  case 6:
                    if (!(_i < _a.length)) return [3, 14];
                    validator = _a[_i];
                    return [4, validator.shouldRefresh(accessTokenPayload, input.userContext)];
                  case 7:
                    if (!_d.sent()) return [3, 13];
                    _d.label = 8;
                  case 8:
                    _d.trys.push([8, 10, , 11]);
                    return [4, validator.refresh(input.userContext)];
                  case 9:
                    _d.sent();
                    return [3, 11];
                  case 10:
                    err_1 = _d.sent();
                    console.error(
                      "Encountered an error while refreshing validator ".concat(validator.id),
                      err_1
                    );
                    return [3, 11];
                  case 11:
                    return [
                      4,
                      this.getAccessTokenPayloadSecurely({
                        userContext: input.userContext
                      })
                    ];
                  case 12:
                    accessTokenPayload = _d.sent();
                    _d.label = 13;
                  case 13:
                    _i++;
                    return [3, 6];
                  case 14:
                    return [3, 17];
                  case 15:
                    (0, logger_1.logDebugMessage)("validateClaims: releasing claim refresh lock");
                    return [4, lockFactory.releaseLock(CLAIM_REFRESH_LOCK_NAME)];
                  case 16:
                    _d.sent();
                    return [
                      7
                      /*endfinally*/
                    ];
                  case 17:
                    return [3, 20];
                  case 18:
                    (0, logger_1.logDebugMessage)("validateClaims: Retrying refresh lock ".concat(tryCount, "/").concat(MAX_REFRESH_LOCK_TRY_COUNT));
                    _d.label = 19;
                  case 19:
                    return [3, 1];
                  case 20:
                    if (!(tryCount === MAX_REFRESH_LOCK_TRY_COUNT)) return [3, 22];
                    (0, logger_1.logDebugMessage)("validateClaims: ran out of retries while trying to acquire claim refresh lock");
                    return [
                      4,
                      this.getAccessTokenPayloadSecurely({ userContext: input.userContext })
                    ];
                  case 21:
                    accessTokenPayload = _d.sent();
                    _d.label = 22;
                  case 22:
                    errors = [];
                    _b = 0, _c = input.claimValidators;
                    _d.label = 23;
                  case 23:
                    if (!(_b < _c.length)) return [3, 26];
                    validator = _c[_b];
                    return [4, validator.validate(accessTokenPayload, input.userContext)];
                  case 24:
                    validationRes = _d.sent();
                    if (!validationRes.isValid) {
                      errors.push({
                        id: validator.id,
                        reason: validationRes.reason
                      });
                    }
                    _d.label = 25;
                  case 25:
                    _b++;
                    return [3, 23];
                  case 26:
                    return [2, errors];
                }
              });
            });
          },
          shouldDoInterceptionBasedOnUrl: function(toCheckUrl, apiDomain, sessionTokenBackendDomain) {
            (0, logger_1.logDebugMessage)(
              "shouldDoInterceptionBasedOnUrl: toCheckUrl: " + toCheckUrl + " apiDomain: " + apiDomain + " sessionTokenBackendDomain: " + sessionTokenBackendDomain
            );
            if (toCheckUrl.includes("superTokensDoNotDoInterception")) {
              return false;
            }
            toCheckUrl = (0, utils_1.normaliseURLDomainOrThrowError)(toCheckUrl);
            var urlObj = new URL(toCheckUrl);
            var domain = urlObj.hostname;
            var apiDomainAndInputDomainMatch = false;
            if (apiDomain !== "") {
              apiDomain = (0, utils_1.normaliseURLDomainOrThrowError)(apiDomain);
              var apiUrlObj = new URL(apiDomain);
              apiDomainAndInputDomainMatch = domain === apiUrlObj.hostname;
            }
            if (sessionTokenBackendDomain === void 0 || apiDomainAndInputDomainMatch) {
              return apiDomainAndInputDomainMatch;
            } else {
              var normalisedsessionDomain = (0, utils_1.normaliseSessionScopeOrThrowError)(sessionTokenBackendDomain);
              return (0, utils_1.matchesDomainOrSubdomain)(domain, normalisedsessionDomain);
            }
          },
          calculateClockSkewInMillis: function(_a) {
            var accessTokenPayload = _a.accessTokenPayload;
            (0, logger_1.logDebugMessage)("calculateClockSkewInMillis: called");
            var tokenIssuedAt = accessTokenPayload === null || accessTokenPayload === void 0 ? void 0 : accessTokenPayload.iat;
            if (tokenIssuedAt === void 0 || typeof tokenIssuedAt !== "number") {
              (0, logger_1.logDebugMessage)(
                "calculateClockSkewInMillis: payload iat is undefined or not a number. This may happen due to an unsupported backend sdk. Returning 0"
              );
              return 0;
            }
            var estimatedServerTimeNow = tokenIssuedAt * 1e3;
            var clockSkewInMillis = estimatedServerTimeNow - Date.now();
            (0, logger_1.logDebugMessage)("calculateClockSkewInMillis: returning " + clockSkewInMillis);
            return clockSkewInMillis;
          }
        };
      }
      exports.default = RecipeImplementation;
    }
  });

  // node_modules/supertokens-js-override/lib/build/getProxyObject.js
  var require_getProxyObject = __commonJS({
    "node_modules/supertokens-js-override/lib/build/getProxyObject.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p))
              t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.getProxyObject = void 0;
      function getProxyObject(orig) {
        var ret = __assign(__assign({}, orig), { _call: function(_, __) {
          throw new Error("This function should only be called through the recipe object");
        } });
        var keys = Object.keys(ret);
        var _loop_1 = function(k2) {
          if (k2 !== "_call") {
            ret[k2] = function() {
              var args = [];
              for (var _i2 = 0; _i2 < arguments.length; _i2++) {
                args[_i2] = arguments[_i2];
              }
              return this._call(k2, args);
            };
          }
        };
        for (var _i = 0, keys_1 = keys; _i < keys_1.length; _i++) {
          var k = keys_1[_i];
          _loop_1(k);
        }
        return ret;
      }
      exports.getProxyObject = getProxyObject;
    }
  });

  // node_modules/supertokens-js-override/lib/build/index.js
  var require_build = __commonJS({
    "node_modules/supertokens-js-override/lib/build/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.OverrideableBuilder = void 0;
      var getProxyObject_1 = require_getProxyObject();
      var OverrideableBuilder = (
        /** @class */
        (function() {
          function OverrideableBuilder2(originalImplementation) {
            this.layers = [originalImplementation];
            this.proxies = [];
          }
          OverrideableBuilder2.prototype.override = function(overrideFunc) {
            var proxy = (0, getProxyObject_1.getProxyObject)(this.layers[0]);
            var layer = overrideFunc(proxy, this);
            for (var _i = 0, _a = Object.keys(this.layers[0]); _i < _a.length; _i++) {
              var key = _a[_i];
              if (layer[key] === proxy[key] || key === "_call") {
                delete layer[key];
              } else if (layer[key] === void 0) {
                layer[key] = null;
              }
            }
            this.layers.push(layer);
            this.proxies.push(proxy);
            return this;
          };
          OverrideableBuilder2.prototype.build = function() {
            var _this = this;
            if (this.result) {
              return this.result;
            }
            this.result = {};
            for (var _i = 0, _a = this.layers; _i < _a.length; _i++) {
              var layer = _a[_i];
              for (var _b = 0, _c = Object.keys(layer); _b < _c.length; _b++) {
                var key = _c[_b];
                var override = layer[key];
                if (override !== void 0) {
                  if (override === null) {
                    this.result[key] = void 0;
                  } else if (typeof override === "function") {
                    this.result[key] = override.bind(this.result);
                  } else {
                    this.result[key] = override;
                  }
                }
              }
            }
            var _loop_1 = function(proxyInd2) {
              var proxy = this_1.proxies[proxyInd2];
              proxy._call = function(fname, args) {
                for (var i = proxyInd2; i >= 0; --i) {
                  var func = _this.layers[i][fname];
                  if (func !== void 0 && func !== null) {
                    return func.bind(_this.result).apply(void 0, args);
                  }
                }
              };
            };
            var this_1 = this;
            for (var proxyInd = 0; proxyInd < this.proxies.length; ++proxyInd) {
              _loop_1(proxyInd);
            }
            return this.result;
          };
          return OverrideableBuilder2;
        })()
      );
      exports.OverrideableBuilder = OverrideableBuilder;
      exports.default = OverrideableBuilder;
    }
  });

  // node_modules/supertokens-website/lib/build/claims/primitiveClaim.js
  var require_primitiveClaim = __commonJS({
    "node_modules/supertokens-website/lib/build/claims/primitiveClaim.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.PrimitiveClaim = void 0;
      var dateProvider_1 = require_dateProvider();
      var PrimitiveClaim = (
        /** @class */
        (function() {
          function PrimitiveClaim2(config) {
            var _this = this;
            this.validators = {
              hasValue: function(val, maxAgeInSeconds, id) {
                if (maxAgeInSeconds === void 0) {
                  maxAgeInSeconds = _this.defaultMaxAgeInSeconds;
                }
                var DateProvider = dateProvider_1.default.getReferenceOrThrow().dateProvider;
                return {
                  id: id !== void 0 ? id : _this.id,
                  refresh: function(ctx) {
                    return _this.refresh(ctx);
                  },
                  shouldRefresh: function(payload, ctx) {
                    if (maxAgeInSeconds !== void 0 && maxAgeInSeconds < DateProvider.getThresholdInSeconds()) {
                      throw new Error(
                        "maxAgeInSeconds must be greater than or equal to the DateProvider threshold value -> ".concat(
                          DateProvider.getThresholdInSeconds()
                        )
                      );
                    }
                    return _this.getValueFromPayload(payload, ctx) === void 0 || // We know payload[this.id] is defined since the value is not undefined in this branch
                    maxAgeInSeconds !== void 0 && payload[_this.id].t < DateProvider.now() - maxAgeInSeconds * 1e3;
                  },
                  validate: function(payload, ctx) {
                    var claimVal = _this.getValueFromPayload(payload, ctx);
                    if (claimVal === void 0) {
                      return {
                        isValid: false,
                        reason: { message: "value does not exist", expectedValue: val, actualValue: claimVal }
                      };
                    }
                    var ageInSeconds = (DateProvider.now() - _this.getLastFetchedTime(payload, ctx)) / 1e3;
                    if (maxAgeInSeconds !== void 0 && ageInSeconds > maxAgeInSeconds) {
                      return {
                        isValid: false,
                        reason: {
                          message: "expired",
                          ageInSeconds,
                          maxAgeInSeconds
                        }
                      };
                    }
                    if (claimVal !== val) {
                      return {
                        isValid: false,
                        reason: { message: "wrong value", expectedValue: val, actualValue: claimVal }
                      };
                    }
                    return { isValid: true };
                  }
                };
              }
            };
            this.id = config.id;
            this.refresh = config.refresh;
            this.defaultMaxAgeInSeconds = config.defaultMaxAgeInSeconds;
          }
          PrimitiveClaim2.prototype.getValueFromPayload = function(payload, _userContext) {
            return payload[this.id] !== void 0 ? payload[this.id].v : void 0;
          };
          PrimitiveClaim2.prototype.getLastFetchedTime = function(payload, _userContext) {
            return payload[this.id] !== void 0 ? payload[this.id].t : void 0;
          };
          return PrimitiveClaim2;
        })()
      );
      exports.PrimitiveClaim = PrimitiveClaim;
    }
  });

  // node_modules/supertokens-website/lib/build/claims/primitiveArrayClaim.js
  var require_primitiveArrayClaim = __commonJS({
    "node_modules/supertokens-website/lib/build/claims/primitiveArrayClaim.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.PrimitiveArrayClaim = void 0;
      var dateProvider_1 = require_dateProvider();
      var PrimitiveArrayClaim = (
        /** @class */
        (function() {
          function PrimitiveArrayClaim2(config) {
            var _this = this;
            this.validators = {
              includes: function(val, maxAgeInSeconds, id) {
                if (maxAgeInSeconds === void 0) {
                  maxAgeInSeconds = _this.defaultMaxAgeInSeconds;
                }
                var DateProvider = dateProvider_1.default.getReferenceOrThrow().dateProvider;
                return {
                  id: id !== void 0 ? id : _this.id,
                  refresh: function(ctx) {
                    return _this.refresh(ctx);
                  },
                  shouldRefresh: function(payload, ctx) {
                    if (maxAgeInSeconds !== void 0 && maxAgeInSeconds < DateProvider.getThresholdInSeconds()) {
                      throw new Error(
                        "maxAgeInSeconds must be greater than or equal to the DateProvider threshold value -> ".concat(
                          DateProvider.getThresholdInSeconds()
                        )
                      );
                    }
                    return _this.getValueFromPayload(payload, ctx) === void 0 || // We know payload[this.id] is defined since the value is not undefined in this branch
                    maxAgeInSeconds !== void 0 && payload[_this.id].t < DateProvider.now() - maxAgeInSeconds * 1e3;
                  },
                  validate: function(payload, ctx) {
                    return __awaiter(_this, void 0, void 0, function() {
                      var claimVal, ageInSeconds;
                      return __generator(this, function(_a) {
                        claimVal = this.getValueFromPayload(payload, ctx);
                        if (claimVal === void 0) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "value does not exist",
                                expectedToInclude: val,
                                actualValue: claimVal
                              }
                            }
                          ];
                        }
                        ageInSeconds = (DateProvider.now() - this.getLastFetchedTime(payload, ctx)) / 1e3;
                        if (maxAgeInSeconds !== void 0 && ageInSeconds > maxAgeInSeconds) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "expired",
                                ageInSeconds,
                                maxAgeInSeconds
                              }
                            }
                          ];
                        }
                        if (!claimVal.includes(val)) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "wrong value",
                                expectedToInclude: val,
                                actualValue: claimVal
                              }
                            }
                          ];
                        }
                        return [2, { isValid: true }];
                      });
                    });
                  }
                };
              },
              excludes: function(val, maxAgeInSeconds, id) {
                if (maxAgeInSeconds === void 0) {
                  maxAgeInSeconds = _this.defaultMaxAgeInSeconds;
                }
                var DateProvider = dateProvider_1.default.getReferenceOrThrow().dateProvider;
                return {
                  id: id !== void 0 ? id : _this.id,
                  refresh: function(ctx) {
                    return _this.refresh(ctx);
                  },
                  shouldRefresh: function(payload, ctx) {
                    if (maxAgeInSeconds !== void 0 && maxAgeInSeconds < DateProvider.getThresholdInSeconds()) {
                      throw new Error(
                        "maxAgeInSeconds must be greater than or equal to the DateProvider threshold value -> ".concat(
                          DateProvider.getThresholdInSeconds()
                        )
                      );
                    }
                    return _this.getValueFromPayload(payload, ctx) === void 0 || // We know payload[this.id] is defined since the value is not undefined in this branch
                    maxAgeInSeconds !== void 0 && payload[_this.id].t < DateProvider.now() - maxAgeInSeconds * 1e3;
                  },
                  validate: function(payload, ctx) {
                    return __awaiter(_this, void 0, void 0, function() {
                      var claimVal, ageInSeconds;
                      return __generator(this, function(_a) {
                        claimVal = this.getValueFromPayload(payload, ctx);
                        if (claimVal === void 0) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "value does not exist",
                                expectedToNotInclude: val,
                                actualValue: claimVal
                              }
                            }
                          ];
                        }
                        ageInSeconds = (DateProvider.now() - this.getLastFetchedTime(payload, ctx)) / 1e3;
                        if (maxAgeInSeconds !== void 0 && ageInSeconds > maxAgeInSeconds) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "expired",
                                ageInSeconds,
                                maxAgeInSeconds
                              }
                            }
                          ];
                        }
                        if (claimVal.includes(val)) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "wrong value",
                                expectedToNotInclude: val,
                                actualValue: claimVal
                              }
                            }
                          ];
                        }
                        return [2, { isValid: true }];
                      });
                    });
                  }
                };
              },
              includesAll: function(val, maxAgeInSeconds, id) {
                if (maxAgeInSeconds === void 0) {
                  maxAgeInSeconds = _this.defaultMaxAgeInSeconds;
                }
                var DateProvider = dateProvider_1.default.getReferenceOrThrow().dateProvider;
                return {
                  id: id !== void 0 ? id : _this.id,
                  refresh: function(ctx) {
                    return _this.refresh(ctx);
                  },
                  shouldRefresh: function(payload, ctx) {
                    if (maxAgeInSeconds !== void 0 && maxAgeInSeconds < DateProvider.getThresholdInSeconds()) {
                      throw new Error(
                        "maxAgeInSeconds must be greater than or equal to the DateProvider threshold value -> ".concat(
                          DateProvider.getThresholdInSeconds()
                        )
                      );
                    }
                    return _this.getValueFromPayload(payload, ctx) === void 0 || // We know payload[this.id] is defined since the value is not undefined in this branch
                    maxAgeInSeconds !== void 0 && payload[_this.id].t < DateProvider.now() - maxAgeInSeconds * 1e3;
                  },
                  validate: function(payload, ctx) {
                    return __awaiter(_this, void 0, void 0, function() {
                      var claimVal, ageInSeconds, claimSet, isValid;
                      return __generator(this, function(_a) {
                        claimVal = this.getValueFromPayload(payload, ctx);
                        if (claimVal === void 0) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "value does not exist",
                                expectedToInclude: val,
                                actualValue: claimVal
                              }
                            }
                          ];
                        }
                        ageInSeconds = (DateProvider.now() - this.getLastFetchedTime(payload, ctx)) / 1e3;
                        if (maxAgeInSeconds !== void 0 && ageInSeconds > maxAgeInSeconds) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "expired",
                                ageInSeconds,
                                maxAgeInSeconds
                              }
                            }
                          ];
                        }
                        claimSet = new Set(claimVal);
                        isValid = val.every(function(v) {
                          return claimSet.has(v);
                        });
                        return [
                          2,
                          isValid ? { isValid } : {
                            isValid,
                            reason: {
                              message: "wrong value",
                              expectedToInclude: val,
                              actualValue: claimVal
                            }
                          }
                        ];
                      });
                    });
                  }
                };
              },
              includesAny: function(val, maxAgeInSeconds, id) {
                if (maxAgeInSeconds === void 0) {
                  maxAgeInSeconds = _this.defaultMaxAgeInSeconds;
                }
                var DateProvider = dateProvider_1.default.getReferenceOrThrow().dateProvider;
                return {
                  id: id !== void 0 ? id : _this.id,
                  refresh: function(ctx) {
                    return _this.refresh(ctx);
                  },
                  shouldRefresh: function(payload, ctx) {
                    if (maxAgeInSeconds !== void 0 && maxAgeInSeconds < DateProvider.getThresholdInSeconds()) {
                      throw new Error(
                        "maxAgeInSeconds must be greater than or equal to the DateProvider threshold value -> ".concat(
                          DateProvider.getThresholdInSeconds()
                        )
                      );
                    }
                    return _this.getValueFromPayload(payload, ctx) === void 0 || // We know payload[this.id] is defined since the value is not undefined in this branch
                    maxAgeInSeconds !== void 0 && payload[_this.id].t < DateProvider.now() - maxAgeInSeconds * 1e3;
                  },
                  validate: function(payload, ctx) {
                    return __awaiter(_this, void 0, void 0, function() {
                      var claimVal, ageInSeconds, claimSet, isValid;
                      return __generator(this, function(_a) {
                        claimVal = this.getValueFromPayload(payload, ctx);
                        if (claimVal === void 0) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "value does not exist",
                                expectedToInclude: val,
                                actualValue: claimVal
                              }
                            }
                          ];
                        }
                        ageInSeconds = (DateProvider.now() - this.getLastFetchedTime(payload, ctx)) / 1e3;
                        if (maxAgeInSeconds !== void 0 && ageInSeconds > maxAgeInSeconds) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "expired",
                                ageInSeconds,
                                maxAgeInSeconds
                              }
                            }
                          ];
                        }
                        claimSet = new Set(claimVal);
                        isValid = val.some(function(v) {
                          return claimSet.has(v);
                        });
                        return [
                          2,
                          isValid ? { isValid } : {
                            isValid,
                            reason: {
                              message: "wrong value",
                              expectedToIncludeAtLeastOneOf: val,
                              actualValue: claimVal
                            }
                          }
                        ];
                      });
                    });
                  }
                };
              },
              excludesAll: function(val, maxAgeInSeconds, id) {
                if (maxAgeInSeconds === void 0) {
                  maxAgeInSeconds = _this.defaultMaxAgeInSeconds;
                }
                var DateProvider = dateProvider_1.default.getReferenceOrThrow().dateProvider;
                return {
                  id: id !== void 0 ? id : _this.id,
                  refresh: function(ctx) {
                    return _this.refresh(ctx);
                  },
                  shouldRefresh: function(payload, ctx) {
                    if (maxAgeInSeconds !== void 0 && maxAgeInSeconds < DateProvider.getThresholdInSeconds()) {
                      throw new Error(
                        "maxAgeInSeconds must be greater than or equal to the DateProvider threshold value -> ".concat(
                          DateProvider.getThresholdInSeconds()
                        )
                      );
                    }
                    return _this.getValueFromPayload(payload, ctx) === void 0 || // We know payload[this.id] is defined since the value is not undefined in this branch
                    maxAgeInSeconds !== void 0 && payload[_this.id].t < DateProvider.now() - maxAgeInSeconds * 1e3;
                  },
                  validate: function(payload, ctx) {
                    return __awaiter(_this, void 0, void 0, function() {
                      var claimVal, ageInSeconds, claimSet, isValid;
                      return __generator(this, function(_a) {
                        claimVal = this.getValueFromPayload(payload, ctx);
                        if (claimVal === void 0) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "value does not exist",
                                expectedToNotInclude: val,
                                actualValue: claimVal
                              }
                            }
                          ];
                        }
                        ageInSeconds = (DateProvider.now() - this.getLastFetchedTime(payload, ctx)) / 1e3;
                        if (maxAgeInSeconds !== void 0 && ageInSeconds > maxAgeInSeconds) {
                          return [
                            2,
                            {
                              isValid: false,
                              reason: {
                                message: "expired",
                                ageInSeconds,
                                maxAgeInSeconds
                              }
                            }
                          ];
                        }
                        claimSet = new Set(claimVal);
                        isValid = val.every(function(v) {
                          return !claimSet.has(v);
                        });
                        return [
                          2,
                          isValid ? { isValid } : {
                            isValid,
                            reason: {
                              message: "wrong value",
                              expectedToNotInclude: val,
                              actualValue: claimVal
                            }
                          }
                        ];
                      });
                    });
                  }
                };
              }
            };
            this.id = config.id;
            this.refresh = config.refresh;
            this.defaultMaxAgeInSeconds = config.defaultMaxAgeInSeconds;
          }
          PrimitiveArrayClaim2.prototype.getValueFromPayload = function(payload, _userContext) {
            return payload[this.id] !== void 0 ? payload[this.id].v : void 0;
          };
          PrimitiveArrayClaim2.prototype.getLastFetchedTime = function(payload, _userContext) {
            return payload[this.id] !== void 0 ? payload[this.id].t : void 0;
          };
          return PrimitiveArrayClaim2;
        })()
      );
      exports.PrimitiveArrayClaim = PrimitiveArrayClaim;
    }
  });

  // node_modules/supertokens-website/lib/build/claims/booleanClaim.js
  var require_booleanClaim = __commonJS({
    "node_modules/supertokens-website/lib/build/claims/booleanClaim.js"(exports) {
      "use strict";
      var __extends = exports && exports.__extends || /* @__PURE__ */ (function() {
        var extendStatics = function(d, b) {
          extendStatics = Object.setPrototypeOf || { __proto__: [] } instanceof Array && function(d2, b2) {
            d2.__proto__ = b2;
          } || function(d2, b2) {
            for (var p in b2) if (Object.prototype.hasOwnProperty.call(b2, p)) d2[p] = b2[p];
          };
          return extendStatics(d, b);
        };
        return function(d, b) {
          if (typeof b !== "function" && b !== null)
            throw new TypeError("Class extends value " + String(b) + " is not a constructor or null");
          extendStatics(d, b);
          function __() {
            this.constructor = d;
          }
          d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
        };
      })();
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.BooleanClaim = void 0;
      var primitiveClaim_1 = require_primitiveClaim();
      var BooleanClaim = (
        /** @class */
        (function(_super) {
          __extends(BooleanClaim2, _super);
          function BooleanClaim2(config) {
            var _this = _super.call(this, config) || this;
            _this.validators = __assign(__assign({}, _this.validators), {
              isTrue: function(maxAge) {
                return _this.validators.hasValue(true, maxAge);
              },
              isFalse: function(maxAge) {
                return _this.validators.hasValue(false, maxAge);
              }
            });
            return _this;
          }
          return BooleanClaim2;
        })(primitiveClaim_1.PrimitiveClaim)
      );
      exports.BooleanClaim = BooleanClaim;
    }
  });

  // node_modules/supertokens-website/lib/build/index.js
  var require_build2 = __commonJS({
    "node_modules/supertokens-website/lib/build/index.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.BooleanClaim = exports.PrimitiveArrayClaim = exports.PrimitiveClaim = exports.getInvalidClaimsFromResponse = exports.getClaimValue = exports.validateClaims = exports.signOut = exports.addAxiosInterceptors = exports.doesSessionExist = exports.attemptRefreshingSession = exports.getAccessToken = exports.getAccessTokenPayloadSecurely = exports.getUserId = exports.init = void 0;
      var fetch_1 = require_fetch();
      var recipeImplementation_1 = require_recipeImplementation();
      var supertokens_js_override_1 = require_build();
      var utils_1 = require_utils();
      var cookieHandler_1 = require_cookieHandler();
      var windowHandler_1 = require_windowHandler();
      var lockFactory_1 = require_lockFactory();
      var sessionClaimValidatorStore_1 = require_sessionClaimValidatorStore();
      var logger_1 = require_logger();
      var dateProvider_1 = require_dateProvider();
      var AuthHttpRequest = (
        /** @class */
        (function() {
          function AuthHttpRequest2() {
          }
          AuthHttpRequest2.init = function(options) {
            cookieHandler_1.default.init(options.cookieHandler);
            windowHandler_1.default.init(options.windowHandler);
            dateProvider_1.default.init(options.dateProvider);
            lockFactory_1.default.init(
              options.lockFactory,
              windowHandler_1.default.getReferenceOrThrow().windowHandler.localStorage
            );
            var config = (0, utils_1.validateAndNormaliseInputOrThrowError)(options);
            if (options.enableDebugLogs !== void 0 && options.enableDebugLogs) {
              (0, logger_1.enableLogging)();
            }
            var recipeImpl = new supertokens_js_override_1.default(
              (0, recipeImplementation_1.default)({
                onHandleEvent: config.onHandleEvent,
                preAPIHook: config.preAPIHook,
                postAPIHook: config.postAPIHook,
                sessionExpiredStatusCode: config.sessionExpiredStatusCode
              })
            ).override(config.override.functions).build();
            fetch_1.default.init(config, recipeImpl);
            AuthHttpRequest2.axiosInterceptorQueue.forEach(function(f) {
              f();
            });
            AuthHttpRequest2.axiosInterceptorQueue = [];
          };
          AuthHttpRequest2.getUserId = function(input) {
            return fetch_1.default.recipeImpl.getUserId({
              userContext: (0, utils_1.getNormalisedUserContext)(input === void 0 ? void 0 : input.userContext)
            });
          };
          AuthHttpRequest2.getAccessTokenPayloadSecurely = function(input) {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_b) {
                return [
                  2,
                  fetch_1.default.recipeImpl.getAccessTokenPayloadSecurely({
                    userContext: (0, utils_1.getNormalisedUserContext)(
                      input === void 0 ? void 0 : input.userContext
                    )
                  })
                ];
              });
            });
          };
          var _a;
          _a = AuthHttpRequest2;
          AuthHttpRequest2.axiosInterceptorQueue = [];
          AuthHttpRequest2.attemptRefreshingSession = function() {
            return __awaiter(void 0, void 0, void 0, function() {
              return __generator(_a, function(_b) {
                return [2, fetch_1.default.attemptRefreshingSession()];
              });
            });
          };
          AuthHttpRequest2.doesSessionExist = function(input) {
            return fetch_1.default.recipeImpl.doesSessionExist({
              userContext: (0, utils_1.getNormalisedUserContext)(input === void 0 ? void 0 : input.userContext)
            });
          };
          AuthHttpRequest2.addAxiosInterceptors = function(axiosInstance, userContext) {
            if (!fetch_1.default.initCalled) {
              AuthHttpRequest2.axiosInterceptorQueue.push(function() {
                fetch_1.default.recipeImpl.addAxiosInterceptors({
                  axiosInstance,
                  userContext: (0, utils_1.getNormalisedUserContext)(userContext)
                });
              });
            } else {
              fetch_1.default.recipeImpl.addAxiosInterceptors({
                axiosInstance,
                userContext: (0, utils_1.getNormalisedUserContext)(userContext)
              });
            }
          };
          AuthHttpRequest2.signOut = function(input) {
            return fetch_1.default.recipeImpl.signOut({
              userContext: (0, utils_1.getNormalisedUserContext)(input === void 0 ? void 0 : input.userContext)
            });
          };
          AuthHttpRequest2.getInvalidClaimsFromResponse = function(input) {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_b) {
                return [
                  2,
                  fetch_1.default.recipeImpl.getInvalidClaimsFromResponse({
                    response: input.response,
                    userContext: (0, utils_1.getNormalisedUserContext)(input.userContext)
                  })
                ];
              });
            });
          };
          AuthHttpRequest2.getClaimValue = function(input) {
            return __awaiter(this, void 0, void 0, function() {
              var userContext, accessTokenPayload;
              return __generator(this, function(_b) {
                switch (_b.label) {
                  case 0:
                    userContext = (0, utils_1.getNormalisedUserContext)(
                      input === void 0 ? void 0 : input.userContext
                    );
                    return [
                      4,
                      AuthHttpRequest2.getAccessTokenPayloadSecurely({ userContext })
                    ];
                  case 1:
                    accessTokenPayload = _b.sent();
                    return [2, input.claim.getValueFromPayload(accessTokenPayload, userContext)];
                }
              });
            });
          };
          AuthHttpRequest2.validateClaims = function(overrideGlobalClaimValidators, userContext) {
            var normalisedUserContext = (0, utils_1.getNormalisedUserContext)(userContext);
            var claimValidatorsAddedByOtherRecipes = sessionClaimValidatorStore_1.SessionClaimValidatorStore.getClaimValidatorsAddedByOtherRecipes();
            var globalClaimValidators = fetch_1.default.recipeImpl.getGlobalClaimValidators({
              claimValidatorsAddedByOtherRecipes,
              userContext: normalisedUserContext
            });
            var claimValidators = overrideGlobalClaimValidators !== void 0 ? overrideGlobalClaimValidators(globalClaimValidators, normalisedUserContext) : globalClaimValidators;
            if (claimValidators.length === 0) {
              return [];
            }
            return fetch_1.default.recipeImpl.validateClaims({
              claimValidators,
              userContext: (0, utils_1.getNormalisedUserContext)(userContext)
            });
          };
          AuthHttpRequest2.getAccessToken = function(input) {
            return __awaiter(void 0, void 0, void 0, function() {
              return __generator(_a, function(_b) {
                switch (_b.label) {
                  case 0:
                    return [
                      4,
                      fetch_1.default.recipeImpl.doesSessionExist({
                        userContext: (0, utils_1.getNormalisedUserContext)(
                          input === void 0 ? void 0 : input.userContext
                        )
                      })
                    ];
                  case 1:
                    if (_b.sent()) {
                      return [2, (0, fetch_1.getTokenForHeaderAuth)("access")];
                    }
                    return [2, void 0];
                }
              });
            });
          };
          return AuthHttpRequest2;
        })()
      );
      exports.default = AuthHttpRequest;
      exports.init = AuthHttpRequest.init;
      exports.getUserId = AuthHttpRequest.getUserId;
      exports.getAccessTokenPayloadSecurely = AuthHttpRequest.getAccessTokenPayloadSecurely;
      exports.getAccessToken = AuthHttpRequest.getAccessToken;
      exports.attemptRefreshingSession = AuthHttpRequest.attemptRefreshingSession;
      exports.doesSessionExist = AuthHttpRequest.doesSessionExist;
      exports.addAxiosInterceptors = AuthHttpRequest.addAxiosInterceptors;
      exports.signOut = AuthHttpRequest.signOut;
      exports.validateClaims = AuthHttpRequest.validateClaims;
      exports.getClaimValue = AuthHttpRequest.getClaimValue;
      exports.getInvalidClaimsFromResponse = AuthHttpRequest.getInvalidClaimsFromResponse;
      var primitiveClaim_1 = require_primitiveClaim();
      Object.defineProperty(exports, "PrimitiveClaim", {
        enumerable: true,
        get: function() {
          return primitiveClaim_1.PrimitiveClaim;
        }
      });
      var primitiveArrayClaim_1 = require_primitiveArrayClaim();
      Object.defineProperty(exports, "PrimitiveArrayClaim", {
        enumerable: true,
        get: function() {
          return primitiveArrayClaim_1.PrimitiveArrayClaim;
        }
      });
      var booleanClaim_1 = require_booleanClaim();
      Object.defineProperty(exports, "BooleanClaim", {
        enumerable: true,
        get: function() {
          return booleanClaim_1.BooleanClaim;
        }
      });
    }
  });

  // node_modules/supertokens-website/index.js
  var require_supertokens_website = __commonJS({
    "node_modules/supertokens-website/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      __export2(require_build2());
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/emailverification/constants.js
  var require_constants2 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/emailverification/constants.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.EMAILVERIFICATION_CLAIM_ID = void 0;
      exports.EMAILVERIFICATION_CLAIM_ID = "st-ev";
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/session/recipe.js
  var require_recipe = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/session/recipe.js"(exports) {
      "use strict";
      var __extends = exports && exports.__extends || /* @__PURE__ */ (function() {
        var extendStatics = function(d, b) {
          extendStatics = Object.setPrototypeOf || { __proto__: [] } instanceof Array && function(d2, b2) {
            d2.__proto__ = b2;
          } || function(d2, b2) {
            for (var p in b2) if (Object.prototype.hasOwnProperty.call(b2, p)) d2[p] = b2[p];
          };
          return extendStatics(d, b);
        };
        return function(d, b) {
          if (typeof b !== "function" && b !== null)
            throw new TypeError("Class extends value " + String(b) + " is not a constructor or null");
          extendStatics(d, b);
          function __() {
            this.constructor = d;
          }
          d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
        };
      })();
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      var __spreadArray = exports && exports.__spreadArray || function(to, from, pack) {
        if (pack || arguments.length === 2)
          for (var i = 0, l = from.length, ar; i < l; i++) {
            if (ar || !(i in from)) {
              if (!ar) ar = Array.prototype.slice.call(from, 0, i);
              ar[i] = from[i];
            }
          }
        return to.concat(ar || Array.prototype.slice.call(from));
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.Recipe = void 0;
      var recipeModule_1 = require_recipeModule();
      var supertokens_website_1 = require_supertokens_website();
      var utils_1 = require_utils2();
      var constants_1 = require_constants2();
      var priorityValidatorIds = [constants_1.EMAILVERIFICATION_CLAIM_ID];
      var Recipe = (
        /** @class */
        (function(_super) {
          __extends(Recipe2, _super);
          function Recipe2(config) {
            var _this = _super.call(this, config) || this;
            _this.getUserId = function(input) {
              return supertokens_website_1.default.getUserId({
                userContext: input.userContext
              });
            };
            _this.getAccessToken = function(input) {
              return __awaiter(_this, void 0, void 0, function() {
                return __generator(this, function(_a) {
                  return [
                    2,
                    supertokens_website_1.default.getAccessToken({
                      userContext: input.userContext
                    })
                  ];
                });
              });
            };
            _this.getAccessTokenPayloadSecurely = function(input) {
              return __awaiter(_this, void 0, void 0, function() {
                return __generator(this, function(_a) {
                  return [
                    2,
                    supertokens_website_1.default.getAccessTokenPayloadSecurely({
                      userContext: input.userContext
                    })
                  ];
                });
              });
            };
            _this.doesSessionExist = function(input) {
              return supertokens_website_1.default.doesSessionExist({
                userContext: input.userContext
              });
            };
            _this.signOut = function(input) {
              return supertokens_website_1.default.signOut({
                userContext: input.userContext
              });
            };
            _this.attemptRefreshingSession = function() {
              return __awaiter(_this, void 0, void 0, function() {
                return __generator(this, function(_a) {
                  return [2, supertokens_website_1.default.attemptRefreshingSession()];
                });
              });
            };
            _this.validateClaims = function(input) {
              return supertokens_website_1.default.validateClaims(input.overrideGlobalClaimValidators, input.userContext);
            };
            supertokens_website_1.default.init(
              __assign(__assign({}, config), {
                override: {
                  functions: function(originalImpl, builder) {
                    var _a;
                    builder.override(function(oI) {
                      return __assign(__assign({}, oI), {
                        getGlobalClaimValidators: function(input) {
                          var res = oI.getGlobalClaimValidators(input);
                          return __spreadArray(
                            __spreadArray(
                              [],
                              res.filter(function(x) {
                                return priorityValidatorIds.includes(x.id);
                              }),
                              true
                            ),
                            res.filter(function(x) {
                              return !priorityValidatorIds.includes(x.id);
                            }),
                            true
                          );
                        }
                      });
                    });
                    if ((_a = config.override) === null || _a === void 0 ? void 0 : _a.functions) {
                      builder.override(config.override.functions);
                    }
                    return originalImpl;
                  }
                },
                preAPIHook: function(context) {
                  return __awaiter(_this, void 0, void 0, function() {
                    var headers, response;
                    return __generator(this, function(_a) {
                      headers = new Headers(context.requestInit.headers);
                      headers.set("rid", config.recipeId);
                      response = __assign(__assign({}, context), {
                        requestInit: __assign(__assign({}, context.requestInit), { headers })
                      });
                      if (config.preAPIHook === void 0) {
                        return [2, response];
                      } else {
                        return [2, config.preAPIHook(context)];
                      }
                      return [
                        2
                        /*return*/
                      ];
                    });
                  });
                },
                apiDomain: config.appInfo.apiDomain.getAsStringDangerous(),
                apiBasePath: config.appInfo.apiBasePath.getAsStringDangerous()
              })
            );
            return _this;
          }
          Recipe2.init = function(config) {
            return function(appInfo, _clientType, enableDebugLogs, overrideMaps) {
              Recipe2.instance = new Recipe2(
                __assign(
                  __assign(
                    {},
                    (0, utils_1.applyPlugins)(
                      Recipe2.RECIPE_ID,
                      config,
                      overrideMaps !== null && overrideMaps !== void 0 ? overrideMaps : []
                    )
                  ),
                  { appInfo, recipeId: Recipe2.RECIPE_ID, enableDebugLogs }
                )
              );
              return Recipe2.instance;
            };
          };
          Recipe2.prototype.getClaimValue = function(input) {
            return supertokens_website_1.default.getClaimValue(input);
          };
          Recipe2.prototype.getInvalidClaimsFromResponse = function(input) {
            return supertokens_website_1.default.getInvalidClaimsFromResponse(input);
          };
          Recipe2.addAxiosInterceptors = function(axiosInstance, userContext) {
            return supertokens_website_1.default.addAxiosInterceptors(axiosInstance, userContext);
          };
          Recipe2.getInstanceOrThrow = function() {
            if (Recipe2.instance === void 0) {
              var error = "No instance of Session found. Ensure that the 'Session.init' method is called within the 'SuperTokens.init' recipeList.";
              error = (0, utils_1.checkForSSRErrorAndAppendIfNeeded)(error);
              throw Error(error);
            }
            return Recipe2.instance;
          };
          Recipe2.reset = function() {
            if (!(0, utils_1.isTest)()) {
              return;
            }
            Recipe2.instance = void 0;
            return;
          };
          Recipe2.RECIPE_ID = "session";
          return Recipe2;
        })(recipeModule_1.default)
      );
      exports.Recipe = Recipe;
      exports.default = Recipe;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/session/index.js
  var require_session = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/session/index.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.getInvalidClaimsFromResponse = exports.getClaimValue = exports.validateClaims = exports.signOut = exports.addAxiosInterceptors = exports.doesSessionExist = exports.attemptRefreshingSession = exports.getAccessToken = exports.getAccessTokenPayloadSecurely = exports.getUserId = exports.init = exports.BooleanClaim = exports.PrimitiveArrayClaim = exports.PrimitiveClaim = void 0;
      var utils_1 = require_utils2();
      var recipe_1 = require_recipe();
      var RecipeWrapper = (
        /** @class */
        (function() {
          function RecipeWrapper2() {
          }
          RecipeWrapper2.init = function(config) {
            return recipe_1.default.init(config);
          };
          RecipeWrapper2.getUserId = function(input) {
            return recipe_1.default.getInstanceOrThrow().getUserId({
              userContext: (0, utils_1.getNormalisedUserContext)(
                input === null || input === void 0 ? void 0 : input.userContext
              )
            });
          };
          RecipeWrapper2.getAccessToken = function(input) {
            return recipe_1.default.getInstanceOrThrow().getAccessToken({
              userContext: (0, utils_1.getNormalisedUserContext)(
                input === null || input === void 0 ? void 0 : input.userContext
              )
            });
          };
          RecipeWrapper2.getAccessTokenPayloadSecurely = function(input) {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                return [
                  2,
                  recipe_1.default.getInstanceOrThrow().getAccessTokenPayloadSecurely({
                    userContext: (0, utils_1.getNormalisedUserContext)(
                      input === null || input === void 0 ? void 0 : input.userContext
                    )
                  })
                ];
              });
            });
          };
          RecipeWrapper2.attemptRefreshingSession = function() {
            return __awaiter(this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                return [2, recipe_1.default.getInstanceOrThrow().attemptRefreshingSession()];
              });
            });
          };
          RecipeWrapper2.doesSessionExist = function(input) {
            return recipe_1.default.getInstanceOrThrow().doesSessionExist({
              userContext: (0, utils_1.getNormalisedUserContext)(
                input === null || input === void 0 ? void 0 : input.userContext
              )
            });
          };
          RecipeWrapper2.addAxiosInterceptors = function(axiosInstance, userContext) {
            return recipe_1.default.addAxiosInterceptors(axiosInstance, (0, utils_1.getNormalisedUserContext)(userContext));
          };
          RecipeWrapper2.signOut = function(input) {
            return recipe_1.default.getInstanceOrThrow().signOut({
              userContext: (0, utils_1.getNormalisedUserContext)(
                input === null || input === void 0 ? void 0 : input.userContext
              )
            });
          };
          RecipeWrapper2.getClaimValue = function(input) {
            return recipe_1.default.getInstanceOrThrow().getClaimValue({
              claim: input.claim,
              userContext: (0, utils_1.getNormalisedUserContext)(
                input === null || input === void 0 ? void 0 : input.userContext
              )
            });
          };
          RecipeWrapper2.validateClaims = function(input) {
            return recipe_1.default.getInstanceOrThrow().validateClaims({
              overrideGlobalClaimValidators: input === null || input === void 0 ? void 0 : input.overrideGlobalClaimValidators,
              userContext: (0, utils_1.getNormalisedUserContext)(
                input === null || input === void 0 ? void 0 : input.userContext
              )
            });
          };
          RecipeWrapper2.getInvalidClaimsFromResponse = function(input) {
            return recipe_1.default.getInstanceOrThrow().getInvalidClaimsFromResponse({
              response: input.response,
              userContext: (0, utils_1.getNormalisedUserContext)(
                input === null || input === void 0 ? void 0 : input.userContext
              )
            });
          };
          return RecipeWrapper2;
        })()
      );
      exports.default = RecipeWrapper;
      var init = RecipeWrapper.init;
      exports.init = init;
      var getUserId = RecipeWrapper.getUserId;
      exports.getUserId = getUserId;
      var getAccessTokenPayloadSecurely = RecipeWrapper.getAccessTokenPayloadSecurely;
      exports.getAccessTokenPayloadSecurely = getAccessTokenPayloadSecurely;
      var getAccessToken = RecipeWrapper.getAccessToken;
      exports.getAccessToken = getAccessToken;
      var attemptRefreshingSession = RecipeWrapper.attemptRefreshingSession;
      exports.attemptRefreshingSession = attemptRefreshingSession;
      var doesSessionExist = RecipeWrapper.doesSessionExist;
      exports.doesSessionExist = doesSessionExist;
      var addAxiosInterceptors = RecipeWrapper.addAxiosInterceptors;
      exports.addAxiosInterceptors = addAxiosInterceptors;
      var signOut = RecipeWrapper.signOut;
      exports.signOut = signOut;
      var validateClaims = RecipeWrapper.validateClaims;
      exports.validateClaims = validateClaims;
      var getClaimValue = RecipeWrapper.getClaimValue;
      exports.getClaimValue = getClaimValue;
      var getInvalidClaimsFromResponse = RecipeWrapper.getInvalidClaimsFromResponse;
      exports.getInvalidClaimsFromResponse = getInvalidClaimsFromResponse;
      var supertokens_website_1 = require_supertokens_website();
      Object.defineProperty(exports, "PrimitiveClaim", {
        enumerable: true,
        get: function() {
          return supertokens_website_1.PrimitiveClaim;
        }
      });
      Object.defineProperty(exports, "PrimitiveArrayClaim", {
        enumerable: true,
        get: function() {
          return supertokens_website_1.PrimitiveArrayClaim;
        }
      });
      Object.defineProperty(exports, "BooleanClaim", {
        enumerable: true,
        get: function() {
          return supertokens_website_1.BooleanClaim;
        }
      });
    }
  });

  // node_modules/supertokens-web-js/recipe/session/index.js
  var require_session2 = __commonJS({
    "node_modules/supertokens-web-js/recipe/session/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      __export2(require_session());
    }
  });

  // node_modules/supertokens-website/utils/cookieHandler/index.js
  var require_cookieHandler2 = __commonJS({
    "node_modules/supertokens-website/utils/cookieHandler/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      __export2(require_cookieHandler());
    }
  });

  // node_modules/supertokens-web-js/lib/build/cookieHandler/index.js
  var require_cookieHandler3 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/cookieHandler/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.CookieHandlerReference = void 0;
      var cookieHandler_1 = require_cookieHandler2();
      Object.defineProperty(exports, "CookieHandlerReference", {
        enumerable: true,
        get: function() {
          return cookieHandler_1.CookieHandlerReference;
        }
      });
    }
  });

  // node_modules/supertokens-web-js/lib/build/postSuperTokensInitCallbacks.js
  var require_postSuperTokensInitCallbacks = __commonJS({
    "node_modules/supertokens-web-js/lib/build/postSuperTokensInitCallbacks.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.PostSuperTokensInitCallbacks = void 0;
      var PostSuperTokensInitCallbacks = (
        /** @class */
        (function() {
          function PostSuperTokensInitCallbacks2() {
          }
          PostSuperTokensInitCallbacks2.addPostInitCallback = function(cb) {
            PostSuperTokensInitCallbacks2.postInitCallbacks.push(cb);
          };
          PostSuperTokensInitCallbacks2.runPostInitCallbacks = function() {
            for (var _i = 0, _a = PostSuperTokensInitCallbacks2.postInitCallbacks; _i < _a.length; _i++) {
              var cb = _a[_i];
              cb();
            }
          };
          PostSuperTokensInitCallbacks2.postInitCallbacks = [];
          return PostSuperTokensInitCallbacks2;
        })()
      );
      exports.PostSuperTokensInitCallbacks = PostSuperTokensInitCallbacks;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/recipeModule/utils.js
  var require_utils3 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/recipeModule/utils.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.normaliseRecipeModuleConfig = void 0;
      function normaliseRecipeModuleConfig(config) {
        var _this = this;
        var preAPIHook = config.preAPIHook;
        if (preAPIHook === void 0) {
          preAPIHook = function(context) {
            return __awaiter(_this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                return [2, context];
              });
            });
          };
        }
        var postAPIHook = config.postAPIHook;
        if (postAPIHook === void 0) {
          postAPIHook = function() {
            return __awaiter(_this, void 0, void 0, function() {
              return __generator(this, function(_a) {
                return [
                  2
                  /*return*/
                ];
              });
            });
          };
        }
        return {
          recipeId: config.recipeId,
          appInfo: config.appInfo,
          clientType: config.clientType,
          preAPIHook,
          postAPIHook
        };
      }
      exports.normaliseRecipeModuleConfig = normaliseRecipeModuleConfig;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/authRecipe/utils.js
  var require_utils4 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/authRecipe/utils.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.normaliseAuthRecipe = void 0;
      var utils_1 = require_utils3();
      function normaliseAuthRecipe(config) {
        return (0, utils_1.normaliseRecipeModuleConfig)(config);
      }
      exports.normaliseAuthRecipe = normaliseAuthRecipe;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/multitenancy/utils.js
  var require_utils5 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/multitenancy/utils.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.normaliseUserInput = void 0;
      var utils_1 = require_utils4();
      function normaliseUserInput(config) {
        var override = __assign(
          {
            functions: function(originalImplementation) {
              return originalImplementation;
            }
          },
          config.override
        );
        return __assign(__assign({}, (0, utils_1.normaliseAuthRecipe)(config)), { override });
      }
      exports.normaliseUserInput = normaliseUserInput;
    }
  });

  // node_modules/supertokens-web-js/lib/build/version.js
  var require_version2 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/version.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.supported_fdi = exports.package_version = void 0;
      exports.package_version = "0.16.0";
      exports.supported_fdi = ["3.1", "4.0", "4.1", "4.2"];
    }
  });

  // node_modules/supertokens-website/utils/error/index.js
  var require_error2 = __commonJS({
    "node_modules/supertokens-website/utils/error/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      var d = require_error();
      if (d.default !== void 0) {
        __export2(d);
      } else {
        __export2({
          default: d,
          ...d
        });
      }
    }
  });

  // node_modules/supertokens-web-js/lib/build/error.js
  var require_error3 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/error.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      var error_1 = require_error2();
      exports.default = error_1.STGeneralError;
    }
  });

  // node_modules/supertokens-web-js/lib/build/querier.js
  var require_querier = __commonJS({
    "node_modules/supertokens-web-js/lib/build/querier.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      var normalisedURLPath_1 = require_normalisedURLPath();
      var version_1 = require_version2();
      var error_1 = require_error3();
      var Querier = (
        /** @class */
        (function() {
          function Querier2(recipeId, appInfo) {
            var _this = this;
            this.recipeId = recipeId;
            this.appInfo = appInfo;
            this.getPath = function(path) {
              var _b, _c;
              var template = typeof path === "string" ? path : path.path;
              var pathParams = typeof path === "string" ? {} : (_b = path.pathParams) !== null && _b !== void 0 ? _b : {};
              var queryParams = typeof path === "string" ? {} : (_c = path.queryParams) !== null && _c !== void 0 ? _c : {};
              var populated = String(template);
              for (var _i = 0, _d = Object.entries(pathParams); _i < _d.length; _i++) {
                var _e = _d[_i], key = _e[0], value = _e[1];
                populated = populated.replace(new RegExp("<".concat(key, ">"), "g"), String(value));
              }
              populated = new normalisedURLPath_1.default(populated).getAsStringDangerous();
              if (populated.startsWith("/public/")) {
                populated = populated.substring(7);
              }
              var searchParams = new URLSearchParams(queryParams);
              var stringifiedSearchParams = searchParams.toString();
              if (stringifiedSearchParams.length > 0) {
                populated += "?" + searchParams.toString();
              }
              return populated;
            };
            this.safelyStringifyBody = function(body) {
              return body ? JSON.stringify(body) : "{}";
            };
            this.get = function(template, config, preAPIHook, postAPIHook) {
              return __awaiter(_this, void 0, void 0, function() {
                var path, result, jsonBody;
                return __generator(this, function(_b) {
                  switch (_b.label) {
                    case 0:
                      path = this.getFullUrl(template);
                      return [
                        4,
                        this.fetch(
                          path,
                          __assign(__assign({ method: "GET" }, config), { body: void 0 }),
                          preAPIHook,
                          postAPIHook
                        )
                      ];
                    case 1:
                      result = _b.sent();
                      return [4, this.getResponseJsonOrThrowGeneralError(result)];
                    case 2:
                      jsonBody = _b.sent();
                      return [
                        2,
                        {
                          jsonBody,
                          fetchResponse: result
                        }
                      ];
                  }
                });
              });
            };
            this.post = function(template, config, preAPIHook, postAPIHook) {
              return __awaiter(_this, void 0, void 0, function() {
                var result, jsonBody;
                return __generator(this, function(_b) {
                  switch (_b.label) {
                    case 0:
                      return [
                        4,
                        this.fetch(
                          this.getFullUrl(template),
                          __assign(__assign({ method: "POST" }, config), {
                            body: this.safelyStringifyBody(config.body)
                          }),
                          preAPIHook,
                          postAPIHook
                        )
                      ];
                    case 1:
                      result = _b.sent();
                      return [4, this.getResponseJsonOrThrowGeneralError(result)];
                    case 2:
                      jsonBody = _b.sent();
                      return [
                        2,
                        {
                          jsonBody,
                          fetchResponse: result
                        }
                      ];
                  }
                });
              });
            };
            this.delete = function(template, config, preAPIHook, postAPIHook) {
              return __awaiter(_this, void 0, void 0, function() {
                var result, jsonBody;
                return __generator(this, function(_b) {
                  switch (_b.label) {
                    case 0:
                      return [
                        4,
                        this.fetch(
                          this.getFullUrl(template),
                          __assign(__assign({ method: "DELETE" }, config), {
                            body: this.safelyStringifyBody(config.body)
                          }),
                          preAPIHook,
                          postAPIHook
                        )
                      ];
                    case 1:
                      result = _b.sent();
                      return [4, this.getResponseJsonOrThrowGeneralError(result)];
                    case 2:
                      jsonBody = _b.sent();
                      return [
                        2,
                        {
                          jsonBody,
                          fetchResponse: result
                        }
                      ];
                  }
                });
              });
            };
            this.put = function(template, config, preAPIHook, postAPIHook) {
              return __awaiter(_this, void 0, void 0, function() {
                var result, jsonBody;
                return __generator(this, function(_b) {
                  switch (_b.label) {
                    case 0:
                      return [
                        4,
                        this.fetch(
                          this.getFullUrl(template),
                          __assign(__assign({ method: "PUT" }, config), {
                            body: this.safelyStringifyBody(config.body)
                          }),
                          preAPIHook,
                          postAPIHook
                        )
                      ];
                    case 1:
                      result = _b.sent();
                      return [4, this.getResponseJsonOrThrowGeneralError(result)];
                    case 2:
                      jsonBody = _b.sent();
                      return [
                        2,
                        {
                          jsonBody,
                          fetchResponse: result
                        }
                      ];
                  }
                });
              });
            };
            this.fetch = function(url, config, preAPIHook, postAPIHook) {
              return __awaiter(_this, void 0, void 0, function() {
                var headers, _b, requestInit, modifiedUrl, result, reponseForPostAPI;
                return __generator(this, function(_c) {
                  switch (_c.label) {
                    case 0:
                      if (config === void 0) {
                        headers = {};
                      } else {
                        headers = config.headers;
                      }
                      return [
                        4,
                        this.callPreAPIHook({
                          preAPIHook,
                          url,
                          requestInit: __assign(__assign({}, config), {
                            headers: __assign(__assign({}, headers), {
                              "fdi-version": version_1.supported_fdi.join(","),
                              "Content-Type": "application/json",
                              rid: this.recipeId
                            })
                          })
                        })
                      ];
                    case 1:
                      _b = _c.sent(), requestInit = _b.requestInit, modifiedUrl = _b.url;
                      return [4, fetch(modifiedUrl, requestInit)];
                    case 2:
                      result = _c.sent();
                      if (result.status >= 300) {
                        throw result;
                      }
                      if (!(postAPIHook !== void 0)) return [3, 4];
                      reponseForPostAPI = result.clone();
                      return [
                        4,
                        postAPIHook({
                          requestInit,
                          url,
                          fetchResponse: reponseForPostAPI
                        })
                      ];
                    case 3:
                      _c.sent();
                      _c.label = 4;
                    case 4:
                      return [2, result];
                  }
                });
              });
            };
            this.callPreAPIHook = function(context) {
              return __awaiter(_this, void 0, void 0, function() {
                var result;
                return __generator(this, function(_b) {
                  switch (_b.label) {
                    case 0:
                      if (context.preAPIHook === void 0) {
                        return [
                          2,
                          {
                            url: context.url,
                            requestInit: context.requestInit
                          }
                        ];
                      }
                      return [
                        4,
                        context.preAPIHook({
                          url: context.url,
                          requestInit: context.requestInit
                        })
                      ];
                    case 1:
                      result = _b.sent();
                      return [2, result];
                  }
                });
              });
            };
            this.getFullUrl = function(path) {
              var basePath = _this.appInfo.apiBasePath.getAsStringDangerous();
              var normalisedPath = _this.getPath(path);
              return "".concat(_this.appInfo.apiDomain.getAsStringDangerous()).concat(basePath).concat(normalisedPath);
            };
            this.getResponseJsonOrThrowGeneralError = function(response) {
              return __awaiter(_this, void 0, void 0, function() {
                var json, message;
                return __generator(this, function(_b) {
                  switch (_b.label) {
                    case 0:
                      return [4, response.clone().json()];
                    case 1:
                      json = _b.sent();
                      if (json.status === "GENERAL_ERROR") {
                        message = json.message === void 0 ? "No Error Message Provided" : json.message;
                        throw new error_1.default(message);
                      }
                      return [2, json];
                  }
                });
              });
            };
          }
          var _a;
          _a = Querier2;
          Querier2.preparePreAPIHook = function(_b) {
            var recipePreAPIHook = _b.recipePreAPIHook, action = _b.action, options = _b.options, userContext = _b.userContext;
            return function(context) {
              return __awaiter(void 0, void 0, void 0, function() {
                var postRecipeHookContext;
                return __generator(_a, function(_b2) {
                  switch (_b2.label) {
                    case 0:
                      return [
                        4,
                        recipePreAPIHook(
                          __assign(__assign({}, context), { action, userContext })
                        )
                      ];
                    case 1:
                      postRecipeHookContext = _b2.sent();
                      if (options === void 0 || options.preAPIHook === void 0) {
                        return [2, postRecipeHookContext];
                      }
                      return [
                        2,
                        options.preAPIHook({
                          url: postRecipeHookContext.url,
                          requestInit: postRecipeHookContext.requestInit,
                          userContext
                        })
                      ];
                  }
                });
              });
            };
          };
          Querier2.preparePostAPIHook = function(_b) {
            var recipePostAPIHook = _b.recipePostAPIHook, action = _b.action, userContext = _b.userContext;
            return function(context) {
              return __awaiter(void 0, void 0, void 0, function() {
                return __generator(_a, function(_b2) {
                  switch (_b2.label) {
                    case 0:
                      return [
                        4,
                        recipePostAPIHook(
                          __assign(__assign({}, context), { userContext, action })
                        )
                      ];
                    case 1:
                      _b2.sent();
                      return [
                        2
                        /*return*/
                      ];
                  }
                });
              });
            };
          };
          return Querier2;
        })()
      );
      exports.default = Querier;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/multitenancy/recipeImplementation.js
  var require_recipeImplementation2 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/multitenancy/recipeImplementation.js"(exports) {
      "use strict";
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.getRecipeImplementation = void 0;
      var querier_1 = require_querier();
      var utils_1 = require_utils2();
      function getRecipeImplementation(recipeImplInput) {
        var querier = new querier_1.default(recipeImplInput.recipeId, recipeImplInput.appInfo);
        return {
          getTenantId: function() {
            var queryParam = (0, utils_1.getQueryParams)("tenantId");
            if ((queryParam === null || queryParam === void 0 ? void 0 : queryParam.trim()) === "") {
              return void 0;
            }
            return queryParam;
          },
          getLoginMethods: function(_a) {
            var tenantId = _a.tenantId, options = _a.options, userContext = _a.userContext;
            return __awaiter(this, void 0, void 0, function() {
              var queryParams, _b, jsonBody, fetchResponse, firstFactors;
              return __generator(this, function(_c) {
                switch (_c.label) {
                  case 0:
                    queryParams = {};
                    if (recipeImplInput.clientType !== void 0) {
                      queryParams.clientType = recipeImplInput.clientType;
                    }
                    return [
                      4,
                      querier.get(
                        {
                          path: "/<tenantId>/loginmethods",
                          pathParams: {
                            tenantId: tenantId || "public"
                          },
                          queryParams
                        },
                        {},
                        querier_1.default.preparePreAPIHook({
                          recipePreAPIHook: recipeImplInput.preAPIHook,
                          action: "GET_LOGIN_METHODS",
                          options,
                          userContext
                        }),
                        querier_1.default.preparePostAPIHook({
                          recipePostAPIHook: recipeImplInput.postAPIHook,
                          action: "GET_LOGIN_METHODS",
                          userContext
                        })
                      )
                    ];
                  case 1:
                    _b = _c.sent(), jsonBody = _b.jsonBody, fetchResponse = _b.fetchResponse;
                    if (jsonBody.firstFactors === void 0) {
                      firstFactors = [];
                      if (jsonBody.emailPassword.enabled) {
                        firstFactors.push("emailpassword");
                      }
                      if (jsonBody.thirdParty.enabled) {
                        firstFactors.push("thirdparty");
                      }
                      if (jsonBody.passwordless.enabled) {
                        firstFactors.push("otp-email");
                        firstFactors.push("otp-phone");
                        firstFactors.push("link-email");
                        firstFactors.push("link-phone");
                      }
                    } else {
                      firstFactors = jsonBody.firstFactors;
                    }
                    return [
                      2,
                      {
                        status: "OK",
                        thirdParty: {
                          providers: jsonBody.thirdParty.providers
                        },
                        firstFactors,
                        fetchResponse
                      }
                    ];
                }
              });
            });
          }
        };
      }
      exports.default = getRecipeImplementation;
      exports.getRecipeImplementation = getRecipeImplementation;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/authRecipe/index.js
  var require_authRecipe = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/authRecipe/index.js"(exports) {
      "use strict";
      var __extends = exports && exports.__extends || /* @__PURE__ */ (function() {
        var extendStatics = function(d, b) {
          extendStatics = Object.setPrototypeOf || { __proto__: [] } instanceof Array && function(d2, b2) {
            d2.__proto__ = b2;
          } || function(d2, b2) {
            for (var p in b2) if (Object.prototype.hasOwnProperty.call(b2, p)) d2[p] = b2[p];
          };
          return extendStatics(d, b);
        };
        return function(d, b) {
          if (typeof b !== "function" && b !== null)
            throw new TypeError("Class extends value " + String(b) + " is not a constructor or null");
          extendStatics(d, b);
          function __() {
            this.constructor = d;
          }
          d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
        };
      })();
      var __awaiter = exports && exports.__awaiter || function(thisArg, _arguments, P, generator) {
        function adopt(value) {
          return value instanceof P ? value : new P(function(resolve2) {
            resolve2(value);
          });
        }
        return new (P || (P = Promise))(function(resolve2, reject) {
          function fulfilled(value) {
            try {
              step(generator.next(value));
            } catch (e) {
              reject(e);
            }
          }
          function rejected(value) {
            try {
              step(generator["throw"](value));
            } catch (e) {
              reject(e);
            }
          }
          function step(result) {
            result.done ? resolve2(result.value) : adopt(result.value).then(fulfilled, rejected);
          }
          step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
      };
      var __generator = exports && exports.__generator || function(thisArg, body) {
        var _ = {
          label: 0,
          sent: function() {
            if (t[0] & 1) throw t[1];
            return t[1];
          },
          trys: [],
          ops: []
        }, f, y, t, g;
        return g = { next: verb(0), throw: verb(1), return: verb(2) }, typeof Symbol === "function" && (g[Symbol.iterator] = function() {
          return this;
        }), g;
        function verb(n) {
          return function(v) {
            return step([n, v]);
          };
        }
        function step(op) {
          if (f) throw new TypeError("Generator is already executing.");
          while (_)
            try {
              if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done)
                return t;
              if (y = 0, t) op = [op[0] & 2, t.value];
              switch (op[0]) {
                case 0:
                case 1:
                  t = op;
                  break;
                case 4:
                  _.label++;
                  return { value: op[1], done: false };
                case 5:
                  _.label++;
                  y = op[1];
                  op = [0];
                  continue;
                case 7:
                  op = _.ops.pop();
                  _.trys.pop();
                  continue;
                default:
                  if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) {
                    _ = 0;
                    continue;
                  }
                  if (op[0] === 3 && (!t || op[1] > t[0] && op[1] < t[3])) {
                    _.label = op[1];
                    break;
                  }
                  if (op[0] === 6 && _.label < t[1]) {
                    _.label = t[1];
                    t = op;
                    break;
                  }
                  if (t && _.label < t[2]) {
                    _.label = t[2];
                    _.ops.push(op);
                    break;
                  }
                  if (t[2]) _.ops.pop();
                  _.trys.pop();
                  continue;
              }
              op = body.call(thisArg, _);
            } catch (e) {
              op = [6, e];
              y = 0;
            } finally {
              f = t = 0;
            }
          if (op[0] & 5) throw op[1];
          return { value: op[0] ? op[1] : void 0, done: true };
        }
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      var recipeModule_1 = require_recipeModule();
      var recipe_1 = require_recipe();
      var AuthRecipe = (
        /** @class */
        (function(_super) {
          __extends(AuthRecipe2, _super);
          function AuthRecipe2(config) {
            var _this = _super.call(this, config) || this;
            _this.signOut = function(input) {
              return __awaiter(_this, void 0, void 0, function() {
                return __generator(this, function(_a) {
                  switch (_a.label) {
                    case 0:
                      return [
                        4,
                        recipe_1.default.getInstanceOrThrow().signOut({
                          userContext: input.userContext
                        })
                      ];
                    case 1:
                      return [2, _a.sent()];
                  }
                });
              });
            };
            return _this;
          }
          return AuthRecipe2;
        })(recipeModule_1.default)
      );
      exports.default = AuthRecipe;
    }
  });

  // node_modules/supertokens-web-js/lib/build/recipe/multitenancy/recipe.js
  var require_recipe2 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/recipe/multitenancy/recipe.js"(exports) {
      "use strict";
      var __extends = exports && exports.__extends || /* @__PURE__ */ (function() {
        var extendStatics = function(d, b) {
          extendStatics = Object.setPrototypeOf || { __proto__: [] } instanceof Array && function(d2, b2) {
            d2.__proto__ = b2;
          } || function(d2, b2) {
            for (var p in b2) if (Object.prototype.hasOwnProperty.call(b2, p)) d2[p] = b2[p];
          };
          return extendStatics(d, b);
        };
        return function(d, b) {
          if (typeof b !== "function" && b !== null)
            throw new TypeError("Class extends value " + String(b) + " is not a constructor or null");
          extendStatics(d, b);
          function __() {
            this.constructor = d;
          }
          d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
        };
      })();
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.Recipe = void 0;
      var utils_1 = require_utils5();
      var supertokens_js_override_1 = require_build();
      var recipeImplementation_1 = require_recipeImplementation2();
      var utils_2 = require_utils2();
      var authRecipe_1 = require_authRecipe();
      var Recipe = (
        /** @class */
        (function(_super) {
          __extends(Recipe2, _super);
          function Recipe2(config) {
            var _this = _super.call(this, (0, utils_1.normaliseUserInput)(config)) || this;
            var builder = new supertokens_js_override_1.default(
              (0, recipeImplementation_1.default)({
                recipeId: _this.config.recipeId,
                appInfo: _this.config.appInfo,
                clientType: _this.config.clientType,
                preAPIHook: _this.config.preAPIHook,
                postAPIHook: _this.config.postAPIHook
              })
            );
            _this.recipeImplementation = builder.override(_this.config.override.functions).build();
            return _this;
          }
          Recipe2.init = function(config) {
            return function(appInfo, clientType, _enableDebugLogs, overrideMaps) {
              Recipe2.instance = new Recipe2(
                __assign(
                  __assign(
                    {},
                    (0, utils_2.applyPlugins)(
                      Recipe2.RECIPE_ID,
                      config,
                      overrideMaps !== null && overrideMaps !== void 0 ? overrideMaps : []
                    )
                  ),
                  { recipeId: Recipe2.RECIPE_ID, appInfo, clientType }
                )
              );
              return Recipe2.instance;
            };
          };
          Recipe2.getInstanceOrThrow = function() {
            if (Recipe2.instance === void 0) {
              var error = "No instance of Multitenancy found. Ensure that 'SuperTokens.init' method has been called.";
              error = (0, utils_2.checkForSSRErrorAndAppendIfNeeded)(error);
              throw Error(error);
            }
            return Recipe2.instance;
          };
          Recipe2.reset = function() {
            if (!(0, utils_2.isTest)()) {
              return;
            }
            Recipe2.instance = void 0;
            return;
          };
          Recipe2.RECIPE_ID = "multitenancy";
          return Recipe2;
        })(authRecipe_1.default)
      );
      exports.Recipe = Recipe;
      exports.default = Recipe;
    }
  });

  // node_modules/supertokens-website/utils/dateProvider/index.js
  var require_dateProvider2 = __commonJS({
    "node_modules/supertokens-website/utils/dateProvider/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      __export2(require_dateProvider());
    }
  });

  // node_modules/supertokens-web-js/lib/build/dateProvider/index.js
  var require_dateProvider3 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/dateProvider/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.DateProviderReference = void 0;
      var dateProvider_1 = require_dateProvider2();
      Object.defineProperty(exports, "DateProviderReference", {
        enumerable: true,
        get: function() {
          return dateProvider_1.DateProviderReference;
        }
      });
    }
  });

  // node_modules/supertokens-web-js/lib/build/versionChecker.js
  var require_versionChecker = __commonJS({
    "node_modules/supertokens-web-js/lib/build/versionChecker.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.isVersionCompatible = void 0;
      var parseVersion = function(version) {
        var match = version.match(/^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/);
        if (!match) {
          throw new Error("Invalid version format: ".concat(version));
        }
        return {
          major: parseInt(match[1]),
          minor: parseInt(match[2]),
          patch: parseInt(match[3]),
          prerelease: match[4]
        };
      };
      var compareVersions = function(a, b) {
        if (a.major !== b.major) return a.major - b.major;
        if (a.minor !== b.minor) return a.minor - b.minor;
        if (a.patch !== b.patch) return a.patch - b.patch;
        if (a.prerelease && !b.prerelease) return -1;
        if (!a.prerelease && b.prerelease) return 1;
        if (a.prerelease && b.prerelease) {
          return a.prerelease.localeCompare(b.prerelease);
        }
        return 0;
      };
      var satisfiesRange = function(version, range) {
        var parsedVersion = parseVersion(version);
        if (range === version) return true;
        var rangeMatch = range.match(/^([<>=~^]+)\s*(.+)$/);
        if (rangeMatch) {
          var operator = rangeMatch[1];
          var rangeVersion = rangeMatch[2];
          var parsedRangeVersion = parseVersion(rangeVersion);
          var comparison = compareVersions(parsedVersion, parsedRangeVersion);
          switch (operator) {
            case ">=":
              return comparison >= 0;
            case ">":
              return comparison > 0;
            case "<=":
              return comparison <= 0;
            case "<":
              return comparison < 0;
            case "=":
            case "==":
              return comparison === 0;
            case "~":
              return parsedVersion.major === parsedRangeVersion.major && parsedVersion.minor === parsedRangeVersion.minor && parsedVersion.patch >= parsedRangeVersion.patch;
            case "^":
              if (parsedRangeVersion.major === 0) {
                return parsedVersion.major === 0 && parsedVersion.minor === parsedRangeVersion.minor && parsedVersion.patch >= parsedRangeVersion.patch;
              } else {
                return parsedVersion.major === parsedRangeVersion.major && parsedVersion.minor >= parsedRangeVersion.minor;
              }
            default:
              return false;
          }
        }
        var xRangeMatch = range.match(/^(\d+)\.(\d+)\.x$/);
        if (xRangeMatch) {
          return parsedVersion.major === parseInt(xRangeMatch[1], 10) && parsedVersion.minor === parseInt(xRangeMatch[2], 10);
        }
        var wildcardMatch = range.match(/^(\d+)\.x$/);
        if (wildcardMatch) {
          return parsedVersion.major === parseInt(wildcardMatch[1], 10);
        }
        var exactRangeVersion = parseVersion(range);
        return compareVersions(parsedVersion, exactRangeVersion) === 0;
      };
      var isVersionCompatible = function(currentVersion, constraints) {
        var constraintArray = Array.isArray(constraints) ? constraints : [constraints];
        for (var _i = 0, constraintArray_1 = constraintArray; _i < constraintArray_1.length; _i++) {
          var constraint = constraintArray_1[_i];
          if (satisfiesRange(currentVersion, constraint)) {
            return true;
          }
        }
        return false;
      };
      exports.isVersionCompatible = isVersionCompatible;
    }
  });

  // node_modules/supertokens-web-js/lib/build/supertokens.js
  var require_supertokens = __commonJS({
    "node_modules/supertokens-web-js/lib/build/supertokens.js"(exports) {
      "use strict";
      var __assign = exports && exports.__assign || function() {
        __assign = Object.assign || function(t) {
          for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p)) t[p] = s[p];
          }
          return t;
        };
        return __assign.apply(this, arguments);
      };
      var __rest = exports && exports.__rest || function(s, e) {
        var t = {};
        for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p) && e.indexOf(p) < 0) t[p] = s[p];
        if (s != null && typeof Object.getOwnPropertySymbols === "function")
          for (var i = 0, p = Object.getOwnPropertySymbols(s); i < p.length; i++) {
            if (e.indexOf(p[i]) < 0 && Object.prototype.propertyIsEnumerable.call(s, p[i])) t[p[i]] = s[p[i]];
          }
        return t;
      };
      Object.defineProperty(exports, "__esModule", { value: true });
      var utils_1 = require_utils2();
      var cookieHandler_1 = require_cookieHandler3();
      var windowHandler_1 = require_windowHandler3();
      var postSuperTokensInitCallbacks_1 = require_postSuperTokensInitCallbacks();
      var recipe_1 = require_recipe2();
      var dateProvider_1 = require_dateProvider3();
      var version_1 = require_version2();
      var versionChecker_1 = require_versionChecker();
      var SuperTokens2 = (
        /** @class */
        (function() {
          function SuperTokens3(config) {
            var _this = this;
            var _a;
            this.recipeList = [];
            this.pluginList = [];
            this.appInfo = (0, utils_1.normaliseInputAppInfoOrThrowError)(config.appInfo);
            if (config.recipeList === void 0 || config.recipeList.length === 0) {
              throw new Error(
                "Please provide at least one recipe to the supertokens.init function call. See https://supertokens.io/docs/emailpassword/quick-setup/frontend"
              );
            }
            var finalPluginList = [];
            if ((_a = config.experimental) === null || _a === void 0 ? void 0 : _a.plugins) {
              for (var _i = 0, _b = config.experimental.plugins; _i < _b.length; _i++) {
                var plugin = _b[_i];
                if (plugin.compatibleWebJSSDKVersions) {
                  var versionCheck = (0, versionChecker_1.isVersionCompatible)(
                    version_1.package_version,
                    plugin.compatibleWebJSSDKVersions
                  );
                  if (!versionCheck) {
                    throw new Error(
                      "Incompatible SDK version for plugin ".concat(plugin.id, '. Version "').concat(version_1.package_version, '" not found in compatible versions: ').concat(JSON.stringify(plugin.compatibleWebJSSDKVersions))
                    );
                  }
                }
                if (plugin.dependencies) {
                  var result = plugin.dependencies(
                    (0, utils_1.getPublicConfig)(__assign(__assign({}, config), { appInfo: this.appInfo })),
                    finalPluginList.map(utils_1.getPublicPlugin),
                    version_1.package_version
                  );
                  if (result.status === "ERROR") {
                    throw new Error(result.message);
                  }
                  if (result.pluginsToAdd) {
                    finalPluginList.push.apply(finalPluginList, result.pluginsToAdd);
                  }
                }
                finalPluginList.push(plugin);
              }
            }
            var duplicatePluginIds = finalPluginList.filter(function(plugin2, index) {
              return finalPluginList.some(function(elem, idx) {
                return elem.id === plugin2.id && idx !== index;
              });
            });
            if (duplicatePluginIds.length > 0) {
              throw new Error(
                "Duplicate plugin IDs: ".concat(
                  duplicatePluginIds.map(function(plugin2) {
                    return plugin2.id;
                  }).join(", ")
                )
              );
            }
            this.pluginList = finalPluginList.map(utils_1.getPublicPlugin);
            var _loop_1 = function(pluginIndex2) {
              var plugin2 = finalPluginList[pluginIndex2];
              if (plugin2.config) {
                var _c = plugin2.config(
                  (0, utils_1.getPublicConfig)(__assign(__assign({}, config), { appInfo: this_1.appInfo }))
                ) || {}, appInfo = _c.appInfo, pluginConfig = __rest(_c, ["appInfo"]);
                config = __assign(__assign({}, config), pluginConfig);
              }
              var pluginInit = finalPluginList[pluginIndex2].init;
              if (pluginInit) {
                postSuperTokensInitCallbacks_1.PostSuperTokensInitCallbacks.addPostInitCallback(function() {
                  pluginInit(
                    (0, utils_1.getPublicConfig)(__assign(__assign({}, config), { appInfo: _this.appInfo })),
                    _this.pluginList,
                    version_1.package_version
                  );
                  _this.pluginList[pluginIndex2].initialized = true;
                });
              }
            };
            var this_1 = this;
            for (var pluginIndex = 0; pluginIndex < this.pluginList.length; pluginIndex += 1) {
              _loop_1(pluginIndex);
            }
            var overrideMaps = finalPluginList.filter(function(p) {
              return p.overrideMap !== void 0;
            }).map(function(p) {
              return p.overrideMap;
            });
            var enableDebugLogs = false;
            if (config.enableDebugLogs !== void 0) {
              enableDebugLogs = config.enableDebugLogs;
            }
            var multitenancyFound = false;
            this.recipeList = config.recipeList.map(function(recipe) {
              var recipeInstance = recipe(_this.appInfo, config.clientType, enableDebugLogs, overrideMaps);
              if (recipeInstance.config.recipeId === recipe_1.Recipe.RECIPE_ID) {
                multitenancyFound = true;
              }
              return recipeInstance;
            });
            if (!multitenancyFound) {
              this.recipeList.push(
                recipe_1.Recipe.init()(this.appInfo, config.clientType, enableDebugLogs, overrideMaps)
              );
            }
          }
          SuperTokens3.init = function(config) {
            cookieHandler_1.CookieHandlerReference.init(config.cookieHandler);
            windowHandler_1.WindowHandlerReference.init(config.windowHandler);
            dateProvider_1.DateProviderReference.init(config.dateProvider);
            if (SuperTokens3.instance !== void 0) {
              console.warn("SuperTokens was already initialized");
              return;
            }
            SuperTokens3.instance = new SuperTokens3(config);
            postSuperTokensInitCallbacks_1.PostSuperTokensInitCallbacks.runPostInitCallbacks();
          };
          SuperTokens3.getInstanceOrThrow = function() {
            if (SuperTokens3.instance === void 0) {
              var error = "SuperTokens must be initialized before calling this method.";
              error = (0, utils_1.checkForSSRErrorAndAppendIfNeeded)(error);
              throw new Error(error);
            }
            return SuperTokens3.instance;
          };
          SuperTokens3.reset = function() {
            if (!(0, utils_1.isTest)()) {
              console.warn("Calling reset() is only supported during testing");
              return;
            }
            recipe_1.Recipe.reset();
            SuperTokens3.instance = void 0;
            return;
          };
          return SuperTokens3;
        })()
      );
      exports.default = SuperTokens2;
    }
  });

  // node_modules/supertokens-web-js/lib/build/index.js
  var require_build3 = __commonJS({
    "node_modules/supertokens-web-js/lib/build/index.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", { value: true });
      exports.init = void 0;
      var supertokens_1 = require_supertokens();
      var SuperTokensAPIWrapper = (
        /** @class */
        (function() {
          function SuperTokensAPIWrapper2() {
          }
          SuperTokensAPIWrapper2.init = function(config) {
            supertokens_1.default.init(config);
          };
          return SuperTokensAPIWrapper2;
        })()
      );
      exports.default = SuperTokensAPIWrapper;
      exports.init = SuperTokensAPIWrapper.init;
    }
  });

  // node_modules/supertokens-web-js/index.js
  var require_supertokens_web_js = __commonJS({
    "node_modules/supertokens-web-js/index.js"(exports) {
      "use strict";
      function __export2(m) {
        for (var p in m) if (!exports.hasOwnProperty(p)) exports[p] = m[p];
      }
      exports.__esModule = true;
      __export2(require_build3());
    }
  });

  // src/browser.ts
  var browser_exports = {};
  __export(browser_exports, {
    ApiError: () => ApiError,
    AuthManager: () => AuthManager,
    LemmaClient: () => LemmaClient,
    buildAuthUrl: () => buildAuthUrl,
    buildFederatedLogoutUrl: () => buildFederatedLogoutUrl,
    clearTestingToken: () => clearTestingToken,
    getTestingToken: () => getTestingToken,
    resolveSafeRedirectUri: () => resolveSafeRedirectUri,
    setTestingToken: () => setTestingToken
  });

  // src/config.ts
  function fromEnv(key) {
    var _a, _b, _c;
    try {
      const meta = void 0;
      if (meta) {
        return (_b = (_a = meta[`VITE_LEMMA_${key}`]) != null ? _a : meta[`REACT_APP_LEMMA_${key}`]) != null ? _b : meta[`LEMMA_${key}`];
      }
    } catch {
    }
    try {
      const env = (_c = globalThis.process) == null ? void 0 : _c.env;
      if (env) {
        return env[`LEMMA_${key}`];
      }
    } catch {
    }
    return void 0;
  }
  function windowConfig() {
    if (typeof window !== "undefined" && window.__LEMMA_CONFIG__) {
      return window.__LEMMA_CONFIG__;
    }
    return {};
  }
  function resolveConfig(overrides = {}) {
    var _a, _b, _c, _d, _e, _f, _g, _h, _i, _j;
    const win = windowConfig();
    const apiUrl = (_c = (_b = (_a = overrides.apiUrl) != null ? _a : win.apiUrl) != null ? _b : fromEnv("API_URL")) != null ? _c : "https://api.lemma.work";
    const authUrl = (_f = (_e = (_d = overrides.authUrl) != null ? _d : win.authUrl) != null ? _e : fromEnv("AUTH_URL")) != null ? _f : "https://lemma.work/auth";
    const podId = (_h = (_g = overrides.podId) != null ? _g : win.podId) != null ? _h : fromEnv("POD_ID");
    return {
      apiUrl: apiUrl.replace(/\/$/, ""),
      authUrl: authUrl.replace(/\/$/, ""),
      podId,
      timeoutMs: (_i = overrides.timeoutMs) != null ? _i : win.timeoutMs,
      maxRetries: (_j = overrides.maxRetries) != null ? _j : win.maxRetries
    };
  }

  // src/auth.ts
  var import_session2 = __toESM(require_session2(), 1);

  // src/supertokens.ts
  var import_supertokens_web_js = __toESM(require_supertokens_web_js(), 1);
  var import_session = __toESM(require_session2(), 1);
  var APP_NAME = "Lemma";
  var SESSION_API_SUFFIX = "/st/auth";
  var initializedSignature = null;
  var unauthorisedListeners = /* @__PURE__ */ new Set();
  function normalizePath(pathname) {
    const trimmed = pathname.trim();
    if (!trimmed || trimmed === "/") {
      return "";
    }
    const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
    return withLeadingSlash.endsWith("/") ? withLeadingSlash.slice(0, -1) : withLeadingSlash;
  }
  function resolveApiBase(apiUrl) {
    if (typeof window === "undefined") {
      throw new Error("Cookie session support requires a browser environment.");
    }
    if (/^https?:\/\//.test(apiUrl)) {
      const url = new URL(apiUrl);
      const apiPrefix2 = normalizePath(url.pathname);
      return {
        apiDomain: url.origin,
        apiBasePath: `${apiPrefix2}${SESSION_API_SUFFIX}` || SESSION_API_SUFFIX
      };
    }
    const apiPrefix = normalizePath(apiUrl);
    return {
      apiDomain: window.location.origin,
      apiBasePath: `${apiPrefix}${SESSION_API_SUFFIX}` || SESSION_API_SUFFIX
    };
  }
  function ensureCookieSessionSupport(apiUrl, onUnauthorised) {
    if (typeof window === "undefined") {
      return;
    }
    if (onUnauthorised) {
      unauthorisedListeners.add(onUnauthorised);
    }
    const { apiDomain, apiBasePath } = resolveApiBase(apiUrl);
    const signature = `${apiDomain}${apiBasePath}`;
    if (initializedSignature === signature) {
      return;
    }
    if (initializedSignature !== null && initializedSignature !== signature) {
      console.warn(
        `[lemma] SuperTokens was already initialised for ${initializedSignature}; continuing with the existing session config.`
      );
      return;
    }
    import_supertokens_web_js.default.init({
      appInfo: {
        appName: APP_NAME,
        apiDomain,
        apiBasePath
      },
      recipeList: [
        import_session.default.init({
          tokenTransferMethod: "cookie",
          onHandleEvent: (event) => {
            if (event.action === "UNAUTHORISED") {
              unauthorisedListeners.forEach((listener) => listener());
            }
          }
        })
      ]
    });
    initializedSignature = signature;
  }

  // src/auth.ts
  var DEFAULT_BLOCKED_REDIRECT_PATHS = ["/login", "/signup", "/auth"];
  var SUPERTOKENS_FRONTEND_MARKER_KEYS = [
    "sFrontToken",
    "st-last-access-token-update",
    "sIRTFrontend",
    "sAntiCsrf",
    "st-access-token",
    "st-refresh-token"
  ];
  var LOCALSTORAGE_TOKEN_KEY = "lemma_token";
  function readStorageToken() {
    if (typeof window === "undefined") return null;
    try {
      return localStorage.getItem(LOCALSTORAGE_TOKEN_KEY);
    } catch {
      return null;
    }
  }
  function writeStorageToken(token) {
    if (typeof window === "undefined") return;
    try {
      localStorage.setItem(LOCALSTORAGE_TOKEN_KEY, token);
    } catch {
    }
  }
  function removeStorageToken() {
    if (typeof window === "undefined") return;
    try {
      localStorage.removeItem(LOCALSTORAGE_TOKEN_KEY);
    } catch {
    }
  }
  function setTestingToken(token) {
    writeStorageToken(token);
  }
  function getTestingToken() {
    return readStorageToken();
  }
  function clearTestingToken() {
    removeStorageToken();
  }
  function detectInjectedToken() {
    if (typeof window === "undefined") return null;
    const localToken = readStorageToken();
    if (localToken) return localToken;
    return null;
  }
  function normalizePath2(path) {
    const trimmed = path.trim();
    if (!trimmed) return "/";
    if (trimmed === "/") return "/";
    const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
    return withLeadingSlash.endsWith("/") ? withLeadingSlash.slice(0, -1) : withLeadingSlash;
  }
  function resolveAuthPath(basePath, path) {
    const normalizedBase = normalizePath2(basePath);
    if (!path || !path.trim()) {
      return normalizedBase;
    }
    const segment = path.trim().replace(/^\/+/, "");
    if (!segment) {
      return normalizedBase;
    }
    return `${normalizedBase}/${segment}`.replace(/\/{2,}/g, "/");
  }
  function isBlockedLocalPath(pathname, blockedPaths) {
    const normalizedPathname = normalizePath2(pathname);
    return blockedPaths.some((rawBlockedPath) => {
      const blockedPath = normalizePath2(rawBlockedPath);
      return normalizedPathname === blockedPath || normalizedPathname.startsWith(`${blockedPath}/`);
    });
  }
  function normalizeOrigin(rawOrigin) {
    const parsed = new URL(rawOrigin);
    return parsed.origin;
  }
  function normalizeHostnameSuffix(rawSuffix) {
    return rawSuffix.trim().replace(/^\.+/, "").toLowerCase();
  }
  function isLoopbackHost(hostname) {
    const normalized = hostname.toLowerCase().replace(/^\[/, "").replace(/\]$/, "");
    return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1";
  }
  function isAllowedRedirectOrigin(parsed, siteOrigin, options) {
    var _a, _b;
    if (parsed.origin === siteOrigin) {
      return true;
    }
    if (options.allowLoopback && isLoopbackHost(parsed.hostname)) {
      return true;
    }
    for (const allowedOrigin of (_a = options.allowedOrigins) != null ? _a : []) {
      try {
        if (normalizeOrigin(allowedOrigin) === parsed.origin) {
          return true;
        }
      } catch {
      }
    }
    const hostname = parsed.hostname.toLowerCase();
    const siteProtocol = new URL(siteOrigin).protocol;
    return ((_b = options.allowedOriginSuffixes) != null ? _b : []).some((rawSuffix) => {
      const suffix = normalizeHostnameSuffix(rawSuffix);
      const hostnameMatches = Boolean(suffix) && (hostname === suffix || hostname.endsWith(`.${suffix}`));
      return hostnameMatches && (siteProtocol !== "https:" || parsed.protocol === "https:");
    });
  }
  function resolveFallbackRedirectUri(rawFallback, siteOrigin, blockedPaths) {
    const rootFallback = new URL("/", siteOrigin).toString();
    try {
      const parsed = new URL(rawFallback, siteOrigin);
      if (parsed.origin !== siteOrigin || !["http:", "https:"].includes(parsed.protocol)) {
        return rootFallback;
      }
      if (isBlockedLocalPath(parsed.pathname, blockedPaths)) {
        return rootFallback;
      }
      return parsed.toString();
    } catch {
      return rootFallback;
    }
  }
  function buildAuthUrl(authUrl, options = {}) {
    var _a;
    const url = new URL(authUrl);
    url.pathname = resolveAuthPath(url.pathname, options.path);
    for (const [key, value] of Object.entries((_a = options.params) != null ? _a : {})) {
      if (value === null || value === void 0) continue;
      if (Array.isArray(value)) {
        url.searchParams.delete(key);
        for (const item of value) {
          url.searchParams.append(key, String(item));
        }
        continue;
      }
      url.searchParams.set(key, String(value));
    }
    if (options.mode === "signup") {
      url.searchParams.set("show", "signup");
    }
    if (options.redirectUri && options.redirectUri.trim()) {
      url.searchParams.set("redirect_uri", options.redirectUri);
    }
    return url.toString();
  }
  function buildFederatedLogoutUrl(authUrl, options = {}) {
    var _a, _b, _c;
    const url = new URL(authUrl);
    url.pathname = resolveAuthPath(url.pathname, (_a = options.path) != null ? _a : "logout");
    for (const [key, value] of Object.entries((_b = options.params) != null ? _b : {})) {
      if (value === null || value === void 0) continue;
      if (Array.isArray(value)) {
        url.searchParams.delete(key);
        for (const item of value) {
          url.searchParams.append(key, String(item));
        }
        continue;
      }
      url.searchParams.set(key, String(value));
    }
    if (options.redirectUri && options.redirectUri.trim()) {
      url.searchParams.set((_c = options.redirectParam) != null ? _c : "redirect_uri", options.redirectUri);
    }
    return url.toString();
  }
  function resolveSafeRedirectUri(rawValue, options) {
    var _a, _b;
    const siteOrigin = normalizeOrigin(options.siteOrigin);
    const blockedPaths = (_a = options.blockedPaths) != null ? _a : DEFAULT_BLOCKED_REDIRECT_PATHS;
    const fallbackTarget = (_b = options.fallback) != null ? _b : "/";
    const fallback = resolveFallbackRedirectUri(fallbackTarget, siteOrigin, blockedPaths);
    if (!rawValue || !rawValue.trim()) {
      return fallback;
    }
    try {
      const parsed = new URL(rawValue, siteOrigin);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return fallback;
      }
      if (!isAllowedRedirectOrigin(parsed, siteOrigin, options)) {
        return fallback;
      }
      if (parsed.origin === siteOrigin && isBlockedLocalPath(parsed.pathname, blockedPaths)) {
        return fallback;
      }
      return parsed.toString();
    } catch {
      return fallback;
    }
  }
  var AuthManager = class {
    constructor(apiUrl, authUrl) {
      __publicField(this, "apiUrl");
      __publicField(this, "authUrl");
      __publicField(this, "injectedToken");
      __publicField(this, "state", { status: "loading", user: null });
      __publicField(this, "listeners", /* @__PURE__ */ new Set());
      this.apiUrl = apiUrl;
      this.authUrl = authUrl;
      this.injectedToken = detectInjectedToken();
      if (!this.injectedToken) {
        ensureCookieSessionSupport(this.apiUrl, () => this.markUnauthenticated());
      }
    }
    /** Whether requests will use an injected Bearer token (testing mode). */
    get isTokenMode() {
      return this.injectedToken !== null;
    }
    /** The current injected Bearer token, if token-mode auth is active. */
    getBearerToken() {
      return this.injectedToken;
    }
    /** The current auth state. */
    getState() {
      return this.state;
    }
    /** True if currently authenticated (status === "authenticated"). */
    isAuthenticated() {
      return this.state.status === "authenticated";
    }
    /** Subscribe to auth state changes. Returns an unsubscribe function. */
    subscribe(listener) {
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }
    notify() {
      this.listeners.forEach((l) => l(this.state));
    }
    setState(state) {
      this.state = state;
      this.notify();
    }
    assertBrowserContext() {
      if (typeof window === "undefined") {
        throw new Error("This auth method is only available in browser environments.");
      }
    }
    getCookie(name) {
      if (typeof document === "undefined") return void 0;
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const match = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
      return match ? decodeURIComponent(match[1]) : void 0;
    }
    getCookieDomainCandidates() {
      if (typeof window === "undefined") {
        return [void 0];
      }
      const host = window.location.hostname;
      const isIpv4 = /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host);
      const isIpv6 = host.includes(":");
      if (!host || host === "localhost" || isIpv4 || isIpv6) {
        return [void 0];
      }
      const domains = /* @__PURE__ */ new Set();
      const parts = host.split(".").filter(Boolean);
      for (let i = 0; i < parts.length - 1; i += 1) {
        const candidate = parts.slice(i).join(".");
        if (!candidate) continue;
        domains.add(candidate);
        domains.add(`.${candidate}`);
      }
      return [void 0, ...domains];
    }
    expireCookie(name, domain) {
      if (typeof document === "undefined") return;
      const domainPart = domain ? `;domain=${domain}` : "";
      document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;max-age=0;path=/${domainPart};samesite=lax`;
      document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;max-age=0;path=/${domainPart}`;
    }
    /**
     * Defensive cleanup for stale SuperTokens frontend marker cookies/storage.
     * This helps recover when signout/session-expiry paths leave local markers behind.
     */
    clearFrontendSessionMarkers() {
      if (typeof window === "undefined") return;
      for (const key of SUPERTOKENS_FRONTEND_MARKER_KEYS) {
        try {
          window.localStorage.removeItem(key);
        } catch {
        }
        try {
          window.sessionStorage.removeItem(key);
        } catch {
        }
      }
      const domains = this.getCookieDomainCandidates();
      for (const key of SUPERTOKENS_FRONTEND_MARKER_KEYS) {
        for (const domain of domains) {
          this.expireCookie(key, domain);
        }
      }
    }
    applyUnauthenticatedState() {
      const next = { status: "unauthenticated", user: null };
      this.setState(next);
      return next;
    }
    clearInjectedToken() {
      this.injectedToken = null;
      clearTestingToken();
    }
    async rawSignOutViaBackend() {
      const antiCsrf = this.getCookie("sAntiCsrf");
      const headers = {
        Accept: "application/json",
        "Content-Type": "application/json",
        rid: "anti-csrf",
        "fdi-version": "4.2",
        "st-auth-mode": "cookie"
      };
      if (antiCsrf) {
        headers["anti-csrf"] = antiCsrf;
      }
      const separator = this.apiUrl.includes("?") ? "&" : "?";
      const signOutUrl = `${this.apiUrl.replace(/\/$/, "")}/st/auth/signout${separator}superTokensDoNotDoInterception=true`;
      await fetch(signOutUrl, {
        method: "POST",
        credentials: "include",
        headers
      });
    }
    /**
     * Check whether a cookie-backed session is active without mutating auth state.
     */
    async isAuthenticatedViaCookie() {
      if (this.injectedToken) {
        return this.isAuthenticated();
      }
      try {
        const response = await fetch(`${this.apiUrl}/users/me`, {
          method: "GET",
          credentials: "include",
          headers: { Accept: "application/json" }
        });
        return response.status !== 401;
      } catch {
        return false;
      }
    }
    /**
     * Return a browser access token from the session layer.
     * Throws if no token is available.
     */
    async getAccessToken() {
      if (this.injectedToken) {
        return this.injectedToken;
      }
      this.assertBrowserContext();
      ensureCookieSessionSupport(this.apiUrl, () => this.markUnauthenticated());
      const token = await import_session2.default.getAccessToken();
      if (!token) {
        throw new Error("Token unavailable");
      }
      return token;
    }
    /**
     * Force a refresh-token flow and return the new access token.
     */
    async refreshAccessToken() {
      if (this.injectedToken) {
        return this.injectedToken;
      }
      this.assertBrowserContext();
      ensureCookieSessionSupport(this.apiUrl, () => this.markUnauthenticated());
      const refreshed = await import_session2.default.attemptRefreshingSession();
      if (!refreshed) {
        throw new Error("Session refresh failed");
      }
      const token = await import_session2.default.getAccessToken();
      if (!token) {
        throw new Error("Token unavailable");
      }
      return token;
    }
    /**
     * Build request headers for an API call.
     * Uses Bearer token if one was injected, otherwise omits Authorization
     * and lets cookies carry the session.
     */
    getRequestInit(init = {}) {
      const headers = {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...init.headers
      };
      if (this.injectedToken) {
        headers["Authorization"] = `Bearer ${this.injectedToken}`;
      }
      return {
        ...init,
        credentials: this.injectedToken ? "omit" : "include",
        headers
      };
    }
    /**
     * Call GET /users/me to determine auth state.
     * Sets internal state and notifies listeners.
     */
    async checkAuth() {
      this.setState({ status: "loading", user: null });
      if (!this.injectedToken && typeof window !== "undefined") {
        ensureCookieSessionSupport(this.apiUrl, () => this.markUnauthenticated());
        try {
          if (!await import_session2.default.doesSessionExist()) {
            return this.applyUnauthenticatedState();
          }
        } catch {
          return this.applyUnauthenticatedState();
        }
      }
      try {
        const response = await fetch(
          `${this.apiUrl}/users/me`,
          this.getRequestInit({ method: "GET" })
        );
        if (response.status === 401) {
          return this.applyUnauthenticatedState();
        }
        if (!response.ok) {
          return this.applyUnauthenticatedState();
        }
        const user = await response.json();
        const next = { status: "authenticated", user };
        this.setState(next);
        return next;
      } catch {
        return this.applyUnauthenticatedState();
      }
    }
    /**
     * Mark the session as unauthenticated (e.g. after a 401 response).
     * Does NOT redirect — call redirectToAuth() explicitly if desired.
     */
    markUnauthenticated() {
      this.applyUnauthenticatedState();
    }
    /**
     * Sign out the current user session.
     * Returns true when the session is no longer active.
     */
    async signOut() {
      if (this.injectedToken) {
        this.clearInjectedToken();
        this.markUnauthenticated();
        return true;
      }
      this.assertBrowserContext();
      ensureCookieSessionSupport(this.apiUrl, () => this.markUnauthenticated());
      try {
        await import_session2.default.signOut();
      } catch {
      }
      if (await this.isAuthenticatedViaCookie()) {
        try {
          await this.rawSignOutViaBackend();
        } catch {
        }
      }
      this.clearFrontendSessionMarkers();
      const isAuthenticated = await this.isAuthenticatedViaCookie();
      if (!isAuthenticated) {
        this.markUnauthenticated();
      }
      return !isAuthenticated;
    }
    /**
     * Build auth URL for login/signup/custom auth sub-path.
     */
    getAuthUrl(options = {}) {
      return buildAuthUrl(this.authUrl, options);
    }
    /**
     * Build upstream/federated logout URL.
     */
    getFederatedLogoutUrl(options = {}) {
      return buildFederatedLogoutUrl(this.authUrl, options);
    }
    /**
     * Redirect to the auth service, passing the current URL as redirect_uri.
     * After the user authenticates, the auth service should redirect back to
     * the original URL and set the session cookie.
     */
    redirectToAuth(options = {}) {
      var _a;
      if (typeof window === "undefined") {
        return;
      }
      const redirectUri = (_a = options.redirectUri) != null ? _a : window.location.href;
      window.location.href = this.getAuthUrl({ ...options, redirectUri });
    }
    /**
     * Optional full logout flow:
     * 1. clear local SDK/session cookies
     * 2. redirect to auth service logout endpoint to terminate upstream SSO
     */
    async redirectToFederatedLogout(options = {}) {
      var _a, _b;
      this.assertBrowserContext();
      const redirectUri = (_a = options.redirectUri) != null ? _a : window.location.href;
      const localSignOut = (_b = options.localSignOut) != null ? _b : true;
      if (localSignOut) {
        await this.signOut();
      }
      window.location.href = this.getFederatedLogoutUrl({
        ...options,
        redirectUri
      });
    }
  };

  // src/run-utils.ts
  async function sleep(ms, signal) {
    if (!Number.isFinite(ms) || ms <= 0) {
      return;
    }
    await new Promise((resolve2, reject) => {
      const timer = setTimeout(() => {
        signal == null ? void 0 : signal.removeEventListener("abort", onAbort);
        resolve2();
      }, ms);
      const onAbort = () => {
        clearTimeout(timer);
        signal == null ? void 0 : signal.removeEventListener("abort", onAbort);
        reject(new DOMException("Operation aborted", "AbortError"));
      };
      if (signal == null ? void 0 : signal.aborted) {
        clearTimeout(timer);
        reject(new DOMException("Operation aborted", "AbortError"));
        return;
      }
      signal == null ? void 0 : signal.addEventListener("abort", onAbort, { once: true });
    });
  }
  function nextBackoffDelay(attempt, options = {}) {
    var _a, _b, _c;
    const baseMs = (_a = options.baseMs) != null ? _a : 500;
    const maxMs = (_b = options.maxMs) != null ? _b : 6e3;
    const factor = (_c = options.factor) != null ? _c : 2;
    const safeAttempt = Math.max(0, Math.floor(attempt));
    const delay = Math.round(baseMs * Math.pow(factor, safeAttempt));
    return Math.min(Math.max(baseMs, delay), maxMs);
  }
  var RETRYABLE_STATUS = /* @__PURE__ */ new Set([429, 502, 503, 504]);
  function serverRetryAfterMs(retryAfter) {
    if (!retryAfter) {
      return null;
    }
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return Math.min(seconds * 1e3, 3e4);
    }
    const dateMs = Date.parse(retryAfter);
    if (!Number.isNaN(dateMs)) {
      return Math.max(0, Math.min(dateMs - Date.now(), 3e4));
    }
    return null;
  }
  function applyJitter(delayMs, random = Math.random) {
    if (!Number.isFinite(delayMs) || delayMs <= 0) {
      return 0;
    }
    const half = delayMs / 2;
    return Math.round(half + random() * half);
  }
  function retryDelayForStatus(status, attempt, maxRetries, retryAfterHeader, random = Math.random) {
    if (!RETRYABLE_STATUS.has(status) || attempt >= maxRetries) {
      return null;
    }
    const serverMs = serverRetryAfterMs(retryAfterHeader);
    if (serverMs !== null) {
      return serverMs;
    }
    return applyJitter(nextBackoffDelay(attempt), random);
  }

  // src/version.ts
  var SDK_VERSION = "0.5.4";
  var CLIENT_HEADER_NAME = "X-Lemma-Client";
  var CLIENT_HEADER_VALUE = `lemma-sdk-ts/${SDK_VERSION}`;

  // src/http.ts
  var DEFAULT_TIMEOUT_MS = 3e4;
  var DEFAULT_MAX_RETRIES = 2;
  var ApiError = class extends Error {
    constructor(statusCode, message, code, details, rawResponse) {
      super(message);
      __publicField(this, "statusCode", statusCode);
      __publicField(this, "code", code);
      __publicField(this, "details", details);
      __publicField(this, "rawResponse", rawResponse);
      /** Server correlation id (X-Request-Id) when present — quote it in bug reports. */
      __publicField(this, "requestId");
      this.name = "ApiError";
    }
  };
  var UnauthorizedError = class extends ApiError {
    constructor() {
      super(...arguments);
      __publicField(this, "name", "UnauthorizedError");
    }
  };
  var ForbiddenError = class extends ApiError {
    constructor() {
      super(...arguments);
      __publicField(this, "name", "ForbiddenError");
    }
  };
  var NotFoundError = class extends ApiError {
    constructor() {
      super(...arguments);
      __publicField(this, "name", "NotFoundError");
    }
  };
  var ConflictError = class extends ApiError {
    constructor() {
      super(...arguments);
      __publicField(this, "name", "ConflictError");
    }
  };
  var RateLimitError = class extends ApiError {
    constructor(message, code, details, rawResponse, retryAfterMs) {
      super(429, message, code, details, rawResponse);
      __publicField(this, "retryAfterMs", retryAfterMs);
      __publicField(this, "name", "RateLimitError");
    }
  };
  var ServerError = class extends ApiError {
    constructor() {
      super(...arguments);
      __publicField(this, "name", "ServerError");
    }
  };
  var NetworkError = class extends Error {
    constructor(message, cause) {
      super(message);
      __publicField(this, "cause", cause);
      __publicField(this, "name", "NetworkError");
    }
  };
  function apiErrorFromStatus(status, message, code, details, rawResponse, retryAfterMsValue) {
    switch (status) {
      case 401:
        return new UnauthorizedError(status, message, code, details, rawResponse);
      case 403:
        return new ForbiddenError(status, message, code, details, rawResponse);
      case 404:
        return new NotFoundError(status, message, code, details, rawResponse);
      case 409:
        return new ConflictError(status, message, code, details, rawResponse);
      case 429:
        return new RateLimitError(message, code, details, rawResponse, retryAfterMsValue);
      default:
        return status >= 500 ? new ServerError(status, message, code, details, rawResponse) : new ApiError(status, message, code, details, rawResponse);
    }
  }
  var HttpClient = class {
    constructor(apiUrl, auth, options = {}) {
      __publicField(this, "apiUrl", apiUrl);
      __publicField(this, "auth", auth);
      __publicField(this, "timeoutMs");
      __publicField(this, "maxRetries");
      var _a, _b;
      this.timeoutMs = (_a = options.timeoutMs) != null ? _a : DEFAULT_TIMEOUT_MS;
      this.maxRetries = (_b = options.maxRetries) != null ? _b : DEFAULT_MAX_RETRIES;
    }
    getBaseUrl() {
      return this.apiUrl;
    }
    /** fetch with a default timeout, normalizing transport failures into NetworkError. */
    async fetchWithTimeout(url, init, userSignal) {
      var _a;
      const controller = new AbortController();
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, this.timeoutMs);
      const onAbort = () => controller.abort();
      if (userSignal) {
        if (userSignal.aborted) controller.abort();
        else userSignal.addEventListener("abort", onAbort, { once: true });
      }
      try {
        return await fetch(url, { ...init, signal: controller.signal });
      } catch (error) {
        if (timedOut) {
          throw new NetworkError(`Request timed out after ${this.timeoutMs}ms`, error);
        }
        if (userSignal == null ? void 0 : userSignal.aborted) {
          throw error;
        }
        throw new NetworkError(
          `Network request failed: ${String((_a = error == null ? void 0 : error.message) != null ? _a : error)}`,
          error
        );
      } finally {
        clearTimeout(timer);
        userSignal == null ? void 0 : userSignal.removeEventListener("abort", onAbort);
      }
    }
    buildUrl(path, params) {
      let url = `${this.apiUrl}${path}`;
      if (!params) {
        return url;
      }
      const qs = Object.entries(params).filter(([, value]) => value !== void 0 && value !== null).map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`).join("&");
      if (qs) {
        url += `?${qs}`;
      }
      return url;
    }
    mergeHeaders(base, extra) {
      var _a;
      if (!extra) {
        return base;
      }
      const merged = new Headers((_a = base.headers) != null ? _a : {});
      const extraHeaders = new Headers(extra);
      extraHeaders.forEach((value, key) => merged.set(key, value));
      return {
        ...base,
        headers: merged
      };
    }
    async parseError(response) {
      var _a, _b;
      let message = response.statusText || "Request failed";
      let code;
      let details;
      let raw = null;
      try {
        const body = await response.json();
        raw = body;
        if (body && typeof body === "object") {
          const record = body;
          if (typeof record.message === "string") {
            message = record.message;
          }
          if (typeof record.code === "string") {
            code = record.code;
          }
          details = record.details;
        }
      } catch {
      }
      const retryMs = response.status === 429 ? (_a = serverRetryAfterMs(response.headers.get("retry-after"))) != null ? _a : void 0 : void 0;
      const error = apiErrorFromStatus(response.status, message, code, details, raw, retryMs);
      error.requestId = (_b = response.headers.get("x-request-id")) != null ? _b : void 0;
      return error;
    }
    getRequestBody(options) {
      if (options.body === void 0) {
        return void 0;
      }
      if (options.isFormData && options.body instanceof FormData) {
        return options.body;
      }
      return JSON.stringify(options.body);
    }
    buildRequestInit(method, options) {
      var _a;
      const initBase = {
        method,
        body: this.getRequestBody(options),
        signal: options.signal
      };
      const withAuth = options.isFormData ? {
        ...this.auth.getRequestInit(initBase),
        headers: Object.fromEntries(
          Object.entries(
            (_a = this.auth.getRequestInit(initBase).headers) != null ? _a : {}
          ).filter(([key]) => key.toLowerCase() !== "content-type")
        )
      } : this.auth.getRequestInit(initBase);
      const withClient = this.mergeHeaders(withAuth, { [CLIENT_HEADER_NAME]: CLIENT_HEADER_VALUE });
      return this.mergeHeaders(withClient, options.headers);
    }
    async request(method, path, options = {}) {
      var _a;
      const url = this.buildUrl(path, options.params);
      const init = this.buildRequestInit(method, options);
      for (let attempt = 0; ; attempt++) {
        const response = await this.fetchWithTimeout(url, init, options.signal);
        if (response.status === 401) {
          this.auth.markUnauthenticated();
        }
        const retryDelay = retryDelayForStatus(
          response.status,
          attempt,
          this.maxRetries,
          response.headers.get("retry-after")
        );
        if (retryDelay !== null) {
          await sleep(retryDelay, options.signal);
          continue;
        }
        if (!response.ok) {
          throw await this.parseError(response);
        }
        if (response.status === 204) {
          return void 0;
        }
        const contentType = (_a = response.headers.get("content-type")) != null ? _a : "";
        if (contentType.includes("application/json")) {
          return response.json();
        }
        return response.text();
      }
    }
    async stream(path, options = {}) {
      var _a, _b, _c;
      let response;
      try {
        response = await fetch(
          this.buildUrl(path, options.params),
          this.buildRequestInit((_a = options.method) != null ? _a : "GET", {
            ...options,
            headers: {
              Accept: "text/event-stream",
              ...options.headers
            }
          })
        );
      } catch (error) {
        if ((_b = options.signal) == null ? void 0 : _b.aborted) {
          throw error;
        }
        throw new NetworkError(
          `Network request failed: ${String((_c = error == null ? void 0 : error.message) != null ? _c : error)}`,
          error
        );
      }
      if (response.status === 401) {
        this.auth.markUnauthenticated();
      }
      if (!response.ok) {
        throw await this.parseError(response);
      }
      if (!response.body) {
        throw new ApiError(response.status, "Stream response had no body.");
      }
      return response.body;
    }
    async requestBytes(method, path) {
      const url = `${this.apiUrl}${path}`;
      const response = await this.fetchWithTimeout(url, this.auth.getRequestInit({ method }));
      if (response.status === 401) {
        this.auth.markUnauthenticated();
      }
      if (!response.ok) {
        throw await this.parseError(response);
      }
      return response.blob();
    }
  };

  // src/openapi_client/core/ApiError.ts
  var ApiError2 = class extends Error {
    constructor(request2, response, message) {
      super(message);
      __publicField(this, "url");
      __publicField(this, "status");
      __publicField(this, "statusText");
      __publicField(this, "body");
      __publicField(this, "request");
      this.name = "ApiError";
      this.url = response.url;
      this.status = response.status;
      this.statusText = response.statusText;
      this.body = response.body;
      this.request = request2;
    }
  };

  // src/openapi_client/core/CancelablePromise.ts
  var CancelError = class extends Error {
    constructor(message) {
      super(message);
      this.name = "CancelError";
    }
    get isCancelled() {
      return true;
    }
  };
  var _isResolved, _isRejected, _isCancelled, _cancelHandlers, _promise, _resolve, _reject;
  var CancelablePromise = class {
    constructor(executor) {
      __privateAdd(this, _isResolved);
      __privateAdd(this, _isRejected);
      __privateAdd(this, _isCancelled);
      __privateAdd(this, _cancelHandlers);
      __privateAdd(this, _promise);
      __privateAdd(this, _resolve);
      __privateAdd(this, _reject);
      __privateSet(this, _isResolved, false);
      __privateSet(this, _isRejected, false);
      __privateSet(this, _isCancelled, false);
      __privateSet(this, _cancelHandlers, []);
      __privateSet(this, _promise, new Promise((resolve2, reject) => {
        __privateSet(this, _resolve, resolve2);
        __privateSet(this, _reject, reject);
        const onResolve = (value) => {
          if (__privateGet(this, _isResolved) || __privateGet(this, _isRejected) || __privateGet(this, _isCancelled)) {
            return;
          }
          __privateSet(this, _isResolved, true);
          if (__privateGet(this, _resolve)) __privateGet(this, _resolve).call(this, value);
        };
        const onReject = (reason) => {
          if (__privateGet(this, _isResolved) || __privateGet(this, _isRejected) || __privateGet(this, _isCancelled)) {
            return;
          }
          __privateSet(this, _isRejected, true);
          if (__privateGet(this, _reject)) __privateGet(this, _reject).call(this, reason);
        };
        const onCancel = (cancelHandler) => {
          if (__privateGet(this, _isResolved) || __privateGet(this, _isRejected) || __privateGet(this, _isCancelled)) {
            return;
          }
          __privateGet(this, _cancelHandlers).push(cancelHandler);
        };
        Object.defineProperty(onCancel, "isResolved", {
          get: () => __privateGet(this, _isResolved)
        });
        Object.defineProperty(onCancel, "isRejected", {
          get: () => __privateGet(this, _isRejected)
        });
        Object.defineProperty(onCancel, "isCancelled", {
          get: () => __privateGet(this, _isCancelled)
        });
        return executor(onResolve, onReject, onCancel);
      }));
    }
    get [Symbol.toStringTag]() {
      return "Cancellable Promise";
    }
    then(onFulfilled, onRejected) {
      return __privateGet(this, _promise).then(onFulfilled, onRejected);
    }
    catch(onRejected) {
      return __privateGet(this, _promise).catch(onRejected);
    }
    finally(onFinally) {
      return __privateGet(this, _promise).finally(onFinally);
    }
    cancel() {
      if (__privateGet(this, _isResolved) || __privateGet(this, _isRejected) || __privateGet(this, _isCancelled)) {
        return;
      }
      __privateSet(this, _isCancelled, true);
      if (__privateGet(this, _cancelHandlers).length) {
        try {
          for (const cancelHandler of __privateGet(this, _cancelHandlers)) {
            cancelHandler();
          }
        } catch (error) {
          console.warn("Cancellation threw an error", error);
          return;
        }
      }
      __privateGet(this, _cancelHandlers).length = 0;
      if (__privateGet(this, _reject)) __privateGet(this, _reject).call(this, new CancelError("Request aborted"));
    }
    get isCancelled() {
      return __privateGet(this, _isCancelled);
    }
  };
  _isResolved = new WeakMap();
  _isRejected = new WeakMap();
  _isCancelled = new WeakMap();
  _cancelHandlers = new WeakMap();
  _promise = new WeakMap();
  _resolve = new WeakMap();
  _reject = new WeakMap();

  // src/openapi_client/core/OpenAPI.ts
  var OpenAPI = {
    BASE: "",
    VERSION: "3.2.0",
    WITH_CREDENTIALS: false,
    CREDENTIALS: "include",
    TOKEN: void 0,
    USERNAME: void 0,
    PASSWORD: void 0,
    HEADERS: void 0,
    ENCODE_PATH: void 0
  };

  // src/generated.ts
  var DEFAULT_MAX_RETRIES2 = 2;
  var DEFAULT_TIMEOUT_MS2 = 3e4;
  function extractMessage(body, fallback) {
    if (body && typeof body === "object" && typeof body.message === "string") {
      return body.message;
    }
    return fallback;
  }
  function extractCode(body) {
    if (body && typeof body === "object" && typeof body.code === "string") {
      return body.code;
    }
    return void 0;
  }
  function extractDetails(body) {
    if (body && typeof body === "object" && "details" in body) {
      return body.details;
    }
    return void 0;
  }
  var GeneratedClientAdapter = class {
    constructor(apiUrl, auth, options = {}) {
      __publicField(this, "apiUrl", apiUrl);
      __publicField(this, "auth", auth);
      __publicField(this, "maxRetries");
      __publicField(this, "timeoutMs");
      var _a, _b;
      this.maxRetries = (_a = options.maxRetries) != null ? _a : DEFAULT_MAX_RETRIES2;
      this.timeoutMs = (_b = options.timeoutMs) != null ? _b : DEFAULT_TIMEOUT_MS2;
    }
    configure() {
      var _a;
      OpenAPI.BASE = this.apiUrl;
      OpenAPI.WITH_CREDENTIALS = true;
      OpenAPI.CREDENTIALS = this.auth.isTokenMode ? "omit" : "include";
      OpenAPI.TOKEN = (_a = this.auth.getBearerToken()) != null ? _a : void 0;
      OpenAPI.HEADERS = { [CLIENT_HEADER_NAME]: CLIENT_HEADER_VALUE };
    }
    async request(operation) {
      this.configure();
      for (let attempt = 0; ; attempt++) {
        try {
          return await this.runWithTimeout(operation);
        } catch (error) {
          if (error instanceof ApiError2) {
            if (error.status === 401) {
              this.auth.markUnauthenticated();
            }
            const retryDelay = retryDelayForStatus(error.status, attempt, this.maxRetries, null);
            if (retryDelay !== null) {
              await sleep(retryDelay);
              continue;
            }
            throw apiErrorFromStatus(
              error.status,
              extractMessage(error.body, error.message),
              extractCode(error.body),
              extractDetails(error.body),
              error.body
            );
          }
          throw error;
        }
      }
    }
    /**
     * Enforce a per-attempt timeout on the generated client (which exposes no
     * timeout of its own). The generated operation returns a CancelablePromise;
     * on timeout we cancel it (aborting the underlying fetch) and surface a
     * NetworkError, matching HttpClient.fetchWithTimeout. Non-cancelable or
     * disabled-timeout cases fall through untouched.
     */
    async runWithTimeout(operation) {
      const op = operation();
      if (this.timeoutMs <= 0 || !(op instanceof CancelablePromise)) {
        return op;
      }
      let timer;
      try {
        return await Promise.race([
          op,
          new Promise((_, reject) => {
            timer = setTimeout(() => {
              op.cancel();
              reject(new NetworkError(`Request timed out after ${this.timeoutMs}ms`));
            }, this.timeoutMs);
          })
        ]);
      } finally {
        if (timer) {
          clearTimeout(timer);
        }
      }
    }
  };

  // src/openapi_client/core/request.ts
  var isDefined = (value) => {
    return value !== void 0 && value !== null;
  };
  var isString = (value) => {
    return typeof value === "string";
  };
  var isStringWithValue = (value) => {
    return isString(value) && value !== "";
  };
  var isBlob = (value) => {
    return typeof value === "object" && typeof value.type === "string" && typeof value.stream === "function" && typeof value.arrayBuffer === "function" && typeof value.constructor === "function" && typeof value.constructor.name === "string" && /^(Blob|File)$/.test(value.constructor.name) && /^(Blob|File)$/.test(value[Symbol.toStringTag]);
  };
  var isFormData = (value) => {
    return value instanceof FormData;
  };
  var base64 = (str) => {
    try {
      return btoa(str);
    } catch (err) {
      return Buffer.from(str).toString("base64");
    }
  };
  var getQueryString = (params) => {
    const qs = [];
    const append = (key, value) => {
      qs.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
    };
    const process2 = (key, value) => {
      if (isDefined(value)) {
        if (Array.isArray(value)) {
          value.forEach((v) => {
            process2(key, v);
          });
        } else if (typeof value === "object") {
          Object.entries(value).forEach(([k, v]) => {
            process2(`${key}[${k}]`, v);
          });
        } else {
          append(key, value);
        }
      }
    };
    Object.entries(params).forEach(([key, value]) => {
      process2(key, value);
    });
    if (qs.length > 0) {
      return `?${qs.join("&")}`;
    }
    return "";
  };
  var getUrl = (config, options) => {
    const encoder = config.ENCODE_PATH || encodeURI;
    const path = options.url.replace("{api-version}", config.VERSION).replace(/{(.*?)}/g, (substring, group) => {
      var _a;
      if ((_a = options.path) == null ? void 0 : _a.hasOwnProperty(group)) {
        return encoder(String(options.path[group]));
      }
      return substring;
    });
    const url = `${config.BASE}${path}`;
    if (options.query) {
      return `${url}${getQueryString(options.query)}`;
    }
    return url;
  };
  var getFormData = (options) => {
    if (options.formData) {
      const formData = new FormData();
      const process2 = (key, value) => {
        if (isString(value) || isBlob(value)) {
          formData.append(key, value);
        } else {
          formData.append(key, JSON.stringify(value));
        }
      };
      Object.entries(options.formData).filter(([_, value]) => isDefined(value)).forEach(([key, value]) => {
        if (Array.isArray(value)) {
          value.forEach((v) => process2(key, v));
        } else {
          process2(key, value);
        }
      });
      return formData;
    }
    return void 0;
  };
  var resolve = async (options, resolver) => {
    if (typeof resolver === "function") {
      return resolver(options);
    }
    return resolver;
  };
  var getHeaders = async (config, options) => {
    const [token, username, password, additionalHeaders] = await Promise.all([
      resolve(options, config.TOKEN),
      resolve(options, config.USERNAME),
      resolve(options, config.PASSWORD),
      resolve(options, config.HEADERS)
    ]);
    const headers = Object.entries({
      Accept: "application/json",
      ...additionalHeaders,
      ...options.headers
    }).filter(([_, value]) => isDefined(value)).reduce((headers2, [key, value]) => ({
      ...headers2,
      [key]: String(value)
    }), {});
    if (isStringWithValue(token)) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    if (isStringWithValue(username) && isStringWithValue(password)) {
      const credentials = base64(`${username}:${password}`);
      headers["Authorization"] = `Basic ${credentials}`;
    }
    if (options.body !== void 0) {
      if (options.mediaType) {
        headers["Content-Type"] = options.mediaType;
      } else if (isBlob(options.body)) {
        headers["Content-Type"] = options.body.type || "application/octet-stream";
      } else if (isString(options.body)) {
        headers["Content-Type"] = "text/plain";
      } else if (!isFormData(options.body)) {
        headers["Content-Type"] = "application/json";
      }
    }
    return new Headers(headers);
  };
  var getRequestBody = (options) => {
    var _a;
    if (options.body !== void 0) {
      if ((_a = options.mediaType) == null ? void 0 : _a.includes("/json")) {
        return JSON.stringify(options.body);
      } else if (isString(options.body) || isBlob(options.body) || isFormData(options.body)) {
        return options.body;
      } else {
        return JSON.stringify(options.body);
      }
    }
    return void 0;
  };
  var sendRequest = async (config, options, url, body, formData, headers, onCancel) => {
    const controller = new AbortController();
    const request2 = {
      headers,
      body: body != null ? body : formData,
      method: options.method,
      signal: controller.signal
    };
    if (config.WITH_CREDENTIALS) {
      request2.credentials = config.CREDENTIALS;
    }
    onCancel(() => controller.abort());
    return await fetch(url, request2);
  };
  var getResponseHeader = (response, responseHeader) => {
    if (responseHeader) {
      const content = response.headers.get(responseHeader);
      if (isString(content)) {
        return content;
      }
    }
    return void 0;
  };
  var getResponseBody = async (response) => {
    if (response.status !== 204) {
      try {
        const contentType = response.headers.get("Content-Type");
        if (contentType) {
          const jsonTypes = ["application/json", "application/problem+json"];
          const isJSON = jsonTypes.some((type) => contentType.toLowerCase().startsWith(type));
          if (isJSON) {
            return await response.json();
          } else {
            return await response.text();
          }
        }
      } catch (error) {
        console.error(error);
      }
    }
    return void 0;
  };
  var catchErrorCodes = (options, result) => {
    var _a, _b;
    const errors = {
      400: "Bad Request",
      401: "Unauthorized",
      403: "Forbidden",
      404: "Not Found",
      500: "Internal Server Error",
      502: "Bad Gateway",
      503: "Service Unavailable",
      ...options.errors
    };
    const error = errors[result.status];
    if (error) {
      throw new ApiError2(options, result, error);
    }
    if (!result.ok) {
      const errorStatus = (_a = result.status) != null ? _a : "unknown";
      const errorStatusText = (_b = result.statusText) != null ? _b : "unknown";
      const errorBody = (() => {
        try {
          return JSON.stringify(result.body, null, 2);
        } catch (e) {
          return void 0;
        }
      })();
      throw new ApiError2(
        options,
        result,
        `Generic Error: status: ${errorStatus}; status text: ${errorStatusText}; body: ${errorBody}`
      );
    }
  };
  var request = (config, options) => {
    return new CancelablePromise(async (resolve2, reject, onCancel) => {
      try {
        const url = getUrl(config, options);
        const formData = getFormData(options);
        const body = getRequestBody(options);
        const headers = await getHeaders(config, options);
        if (!onCancel.isCancelled) {
          const response = await sendRequest(config, options, url, body, formData, headers, onCancel);
          const responseBody = await getResponseBody(response);
          const responseHeader = getResponseHeader(response, options.responseHeader);
          const result = {
            url,
            ok: response.ok,
            status: response.status,
            statusText: response.statusText,
            body: responseHeader != null ? responseHeader : responseBody
          };
          catchErrorCodes(options, result);
          resolve2(result.body);
        }
      } catch (error) {
        reject(error);
      }
    });
  };

  // src/openapi_client/services/AgentRuntimeService.ts
  var AgentRuntimeService = class {
    /**
     * List Available Agent Harnesses
     * @returns AgentHarnessListResponse Successful Response
     * @throws ApiError
     */
    static agentRuntimeHarnessesList() {
      return request(OpenAPI, {
        method: "GET",
        url: "/agent-runtime/harnesses"
      });
    }
    /**
     * List Available Agent Runtime Profiles
     * @param orgId
     * @returns AgentRuntimeProfileListResponse Successful Response
     * @throws ApiError
     */
    static agentRuntimeProfilesList(orgId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{org_id}/agent-runtime/profiles",
        path: {
          "org_id": orgId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Agent Runtime Profile
     * @param orgId
     * @param requestBody
     * @returns AgentRuntimeProfileResponse Successful Response
     * @throws ApiError
     */
    static agentRuntimeProfilesCreate(orgId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{org_id}/agent-runtime/profiles",
        path: {
          "org_id": orgId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/agent-runtime.ts
  var AgentRuntimeNamespace = class {
    constructor(client) {
      __publicField(this, "client", client);
    }
    listHarnesses() {
      return this.client.request(() => AgentRuntimeService.agentRuntimeHarnessesList());
    }
    listAvailableHarnesses() {
      return this.listHarnesses();
    }
    listRuntimes(orgId) {
      return this.listProfiles(orgId);
    }
    listProfiles(orgId) {
      return this.client.request(() => AgentRuntimeService.agentRuntimeProfilesList(orgId));
    }
    createRuntime(orgId, request2) {
      return this.createProfile(orgId, request2);
    }
    createProfile(orgId, request2) {
      return this.client.request(() => AgentRuntimeService.agentRuntimeProfilesCreate(orgId, request2));
    }
    /**
     * @deprecated Runtime defaults are now pod config (`default_profile_id`) or
     * organization Agent Runtimes. The backend no longer exposes a global
     * default-runtime mutation endpoint.
     */
    updateDefault(agentRuntime) {
      void agentRuntime;
      return Promise.reject(new Error(
        "agentRuntime.updateDefault is no longer supported. Update pod config.default_profile_id instead."
      ));
    }
  };

  // src/openapi_client/services/AgentsService.ts
  var AgentsService = class {
    /**
     * List Agents
     * List pod-owned agent definitions visible to the current user.
     * @param podId
     * @param pageToken
     * @param limit
     * @returns AgentListResponse Successful Response
     * @throws ApiError
     */
    static agentList(podId, pageToken, limit = 100) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/agents",
        path: {
          "pod_id": podId
        },
        query: {
          "page_token": pageToken,
          "limit": limit
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Agent
     * Create a pod-owned agent definition with runtime, toolsets, and schemas.
     * @param podId
     * @param requestBody
     * @returns AgentActionResponse Successful Response
     * @throws ApiError
     */
    static agentCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/agents",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Agent
     * Delete a pod-owned agent definition by name.
     * @param podId
     * @param agentName
     * @returns AgentMessageResponse Successful Response
     * @throws ApiError
     */
    static agentDelete(podId, agentName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/agents/{agent_name}",
        path: {
          "pod_id": podId,
          "agent_name": agentName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Agent
     * Get one pod-owned agent definition by its stable name.
     * @param podId
     * @param agentName
     * @returns AgentDetailResponse Successful Response
     * @throws ApiError
     */
    static agentGet(podId, agentName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/agents/{agent_name}",
        path: {
          "pod_id": podId,
          "agent_name": agentName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Agent
     * Update an agent definition, including prompt instruction, runtime, toolsets, and schemas.
     * @param podId
     * @param agentName
     * @param requestBody
     * @returns AgentActionResponse Successful Response
     * @throws ApiError
     */
    static agentUpdate(podId, agentName, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/agents/{agent_name}",
        path: {
          "pod_id": podId,
          "agent_name": agentName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Agent Resource Permissions
     * Get explicit resource grants assigned to an agent.
     * @param podId
     * @param agentName
     * @returns AgentPermissionsResponse Successful Response
     * @throws ApiError
     */
    static agentPermissionsGet(podId, agentName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/agents/{agent_name}/permissions",
        path: {
          "pod_id": podId,
          "agent_name": agentName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Replace Agent Resource Permissions
     * Replace explicit resource grants assigned to an agent.
     * @param podId
     * @param agentName
     * @param requestBody
     * @returns AgentPermissionsResponse Successful Response
     * @throws ApiError
     */
    static agentPermissionsReplace(podId, agentName, requestBody) {
      return request(OpenAPI, {
        method: "PUT",
        url: "/pods/{pod_id}/agents/{agent_name}/permissions",
        path: {
          "pod_id": podId,
          "agent_name": agentName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/agents.ts
  var AgentsNamespace = class {
    constructor(client, podId, conversations) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
      __publicField(this, "conversations", conversations);
      __publicField(this, "permissions", {
        get: (agentName) => this.client.request(() => AgentsService.agentPermissionsGet(this.podId(), agentName)),
        replace: (agentName, payload) => this.client.request(() => AgentsService.agentPermissionsReplace(this.podId(), agentName, payload))
      });
    }
    list(options = {}) {
      return this.client.request(() => {
        var _a;
        return AgentsService.agentList(this.podId(), options.pageToken, (_a = options.limit) != null ? _a : 100);
      });
    }
    /**
     * Run an agent on a single message (the `.run` verb, alongside
     * `functions.run` / `datastore.query`). Note the return contract differs:
     * those return the result directly, whereas an agent reply is asynchronous —
     * this opens a fresh conversation, sends `message`, and returns the created
     * conversation (read the reply via `client.conversations.messages.list(conv.id)`).
     * With `stream: true` it returns the SSE stream so you can consume tokens as
     * they arrive.
     */
    async run(agentName, message, options = {}) {
      if (!this.conversations) {
        throw new Error(
          "agents.run requires the conversations namespace \u2014 call it via client.agents.run()."
        );
      }
      const conversations = this.conversations();
      const conversation = await conversations.createForAgent(agentName, {
        title: options.title,
        metadata: options.metadata
      });
      if (options.stream) {
        return conversations.sendMessageStream(
          conversation.id,
          { content: message },
          { signal: options.signal }
        );
      }
      await conversations.messages.send(conversation.id, { content: message });
      return conversation;
    }
    create(payload) {
      return this.client.request(() => AgentsService.agentCreate(this.podId(), payload));
    }
    get(agentName) {
      return this.client.request(() => AgentsService.agentGet(this.podId(), agentName));
    }
    update(agentName, payload) {
      return this.client.request(() => AgentsService.agentUpdate(this.podId(), agentName, payload));
    }
    delete(agentName) {
      return this.client.request(() => AgentsService.agentDelete(this.podId(), agentName));
    }
  };

  // src/namespaces/conversations.ts
  function normalizeConversation(conversation) {
    var _a, _b, _c, _d, _e;
    if (!conversation) return conversation;
    const record = conversation;
    return {
      ...record,
      model: (_d = (_c = (_b = record.model) != null ? _b : (_a = record.agent_runtime) == null ? void 0 : _a.model_name) != null ? _c : record.model_name) != null ? _d : null,
      status: (_e = record.status) != null ? _e : "waiting"
    };
  }
  function normalizeConversationList(response) {
    var _a;
    const items = ((_a = response.items) != null ? _a : []).map((conversation) => normalizeConversation(conversation));
    return {
      ...response,
      items
    };
  }
  function normalizeMessage(message) {
    return message;
  }
  var ConversationsNamespace = class {
    constructor(http, podId) {
      __publicField(this, "http", http);
      __publicField(this, "podId", podId);
      __publicField(this, "runtimeCatalogPromise");
      __publicField(this, "profileCatalogPromises", /* @__PURE__ */ new Map());
      __publicField(this, "messages", {
        list: (conversationId, options = {}) => {
          var _a;
          const podId = this.requirePodId(options.pod_id);
          return this.http.request(
            "GET",
            `/pods/${podId}/conversations/${conversationId}/messages`,
            {
              params: {
                page_token: options.page_token,
                before_sequence: options.before_sequence,
                after_sequence: options.after_sequence,
                limit: (_a = options.limit) != null ? _a : 100
              }
            }
          ).then((response) => {
            var _a2;
            return {
              ...response,
              items: ((_a2 = response.items) != null ? _a2 : []).map(normalizeMessage)
            };
          });
        },
        send: (conversationId, payload, options = {}) => {
          const podId = this.requirePodId(options.pod_id);
          return this.http.request("POST", `/pods/${podId}/conversations/${conversationId}/messages`, {
            body: payload
          });
        }
      });
      __publicField(this, "approvals", {
        list: (conversationId, options = {}) => {
          const podId = this.requirePodId(options.pod_id);
          return this.http.request(
            "GET",
            `/pods/${podId}/conversations/${conversationId}/approvals`
          );
        },
        resolve: (conversationId, approvalId, payload, options = {}) => {
          const podId = this.requirePodId(options.pod_id);
          const body = {
            ...payload,
            decision: payload.decision
          };
          return this.http.request(
            "POST",
            `/pods/${podId}/conversations/${conversationId}/approvals/${approvalId}/decision`,
            { body }
          );
        }
      });
    }
    resolvePodId(explicitPodId) {
      if (typeof explicitPodId === "string") {
        return explicitPodId;
      }
      try {
        return this.podId();
      } catch {
        return void 0;
      }
    }
    requirePodId(explicitPodId) {
      const podId = this.resolvePodId(explicitPodId);
      if (!podId) {
        throw new Error("pod_id is required for this conversation operation.");
      }
      return podId;
    }
    listRuntimeCatalog() {
      var _a;
      (_a = this.runtimeCatalogPromise) != null ? _a : this.runtimeCatalogPromise = this.http.request(
        "GET",
        "/agent-runtime/harnesses"
      );
      return this.runtimeCatalogPromise;
    }
    listProfileCatalog(orgId) {
      const key = orgId.trim();
      const existing = this.profileCatalogPromises.get(key);
      if (existing) return existing;
      const request2 = this.http.request(
        "GET",
        `/organizations/${encodeURIComponent(key)}/agent-runtime/profiles`
      );
      this.profileCatalogPromises.set(key, request2);
      return request2;
    }
    modelOptionsFromProfiles(catalog) {
      return catalog.items.flatMap((profile) => {
        var _a;
        const catalogEntries = (_a = profile.model_catalog) != null ? _a : [];
        const entries = catalogEntries.length > 0 ? catalogEntries : profile.default_model_name ? [{
          name: profile.default_model_name,
          display_name: null,
          provider_model_name: profile.default_model_name,
          capabilities: [],
          default_model_settings: {},
          metadata: {}
        }] : [];
        return entries.map((model) => {
          var _a2;
          return {
            id: model.name,
            name: (_a2 = model.display_name) != null ? _a2 : model.name,
            agentRuntime: profile,
            agentRuntimeId: profile.id,
            profile,
            profile_id: profile.id,
            harness_kind: profile.derived_harness_kind,
            description: profile.name,
            runtime: {
              profile_id: profile.id,
              model_name: model.name
            }
          };
        });
      });
    }
    async resolveAgentRuntime(agentRuntime, model, harnessKind, profileId) {
      if (agentRuntime || !model) {
        return agentRuntime;
      }
      if (profileId) {
        return {
          profile_id: profileId,
          model_name: model
        };
      }
      void harnessKind;
      return void 0;
    }
    list(options = {}) {
      var _a;
      const podId = this.requirePodId(options.pod_id);
      return this.http.request("GET", `/pods/${podId}/conversations`, {
        params: {
          agent_name: options.agent_name,
          parent_id: options.parent_id,
          type: options.type,
          limit: (_a = options.limit) != null ? _a : 20,
          page_token: options.page_token
        }
      }).then(normalizeConversationList);
    }
    listByAgent(agentName, options = {}) {
      return this.list({ ...options, agent_name: agentName });
    }
    async listModels(options = {}) {
      var _a;
      const orgId = (_a = options.orgId) == null ? void 0 : _a.trim();
      if (orgId) {
        const catalog2 = await this.listProfileCatalog(orgId);
        const items2 = this.modelOptionsFromProfiles(catalog2);
        return {
          items: items2,
          limit: items2.length,
          next_page_token: null
        };
      }
      const catalog = await this.listRuntimeCatalog();
      const items = catalog.items.flatMap(
        (harness) => {
          var _a2;
          return ((_a2 = harness.models) != null ? _a2 : []).map((model) => ({
            id: model,
            name: model,
            harness_kind: harness.harness_kind,
            description: harness.daemon_display_name
          }));
        }
      );
      return {
        items,
        limit: items.length,
        next_page_token: null
      };
    }
    async create(payload = {}) {
      const podId = this.requirePodId(payload.pod_id);
      const { agent_name, harness_kind, model, model_name, pod_id, profile_id, ...requestBody } = payload;
      const agentRuntime = await this.resolveAgentRuntime(
        requestBody.agent_runtime,
        model_name != null ? model_name : model,
        harness_kind,
        profile_id
      );
      const body = {
        ...requestBody,
        agent_name: agent_name != null ? agent_name : void 0,
        agent_runtime: agentRuntime
      };
      void pod_id;
      return this.http.request("POST", `/pods/${podId}/conversations`, {
        body
      }).then(normalizeConversation);
    }
    createForAgent(agentName, payload = {}) {
      return this.create({
        ...payload,
        agent_name: agentName
      });
    }
    get(conversationId, options = {}) {
      const podId = this.requirePodId(options.pod_id);
      return this.http.request("GET", `/pods/${podId}/conversations/${conversationId}`).then(normalizeConversation);
    }
    async update(conversationId, payload, options = {}) {
      const podId = this.requirePodId(options.pod_id);
      const { harness_kind, model, model_name, profile_id, ...requestBody } = payload;
      const agentRuntime = await this.resolveAgentRuntime(
        requestBody.agent_runtime,
        model_name != null ? model_name : model,
        harness_kind,
        profile_id
      );
      const body = {
        ...requestBody,
        agent_runtime: agentRuntime
      };
      return this.http.request("PATCH", `/pods/${podId}/conversations/${conversationId}`, {
        body
      }).then(normalizeConversation);
    }
    sendMessageStream(conversationId, payload, options = {}) {
      const podId = this.requirePodId(options.pod_id);
      return this.http.stream(`/pods/${podId}/conversations/${conversationId}/messages`, {
        method: "POST",
        body: payload,
        signal: options.signal,
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream"
        }
      });
    }
    resumeStream(conversationId, options = {}) {
      const podId = this.requirePodId(options.pod_id);
      return this.http.stream(`/pods/${podId}/conversations/${conversationId}/stream`, {
        signal: options.signal,
        headers: {
          Accept: "text/event-stream"
        }
      });
    }
    stopRun(conversationId, options = {}) {
      const podId = this.requirePodId(options.pod_id);
      return this.http.request("POST", `/pods/${podId}/conversations/${conversationId}/stop`, {
        body: {}
      }).then(normalizeConversation);
    }
  };

  // src/openapi_client/services/AppsService.ts
  var AppsService = class {
    /**
     * List Apps
     * @param podId
     * @param limit
     * @param pageToken
     * @returns AppListResponse Successful Response
     * @throws ApiError
     */
    static appList(podId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/apps",
        path: {
          "pod_id": podId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create App
     * @param podId
     * @param requestBody
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    static appCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/apps",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Save Widget As App
     * Promote a conversation widget into a persisted app.
     *
     * The widget and the app are the same artifact at two lifecycle stages: this
     * fetches the widget's stored HTML and deploys it as the app's bundle —
     * identical to what was shown.
     * @param podId
     * @param requestBody
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    static appCreateFromWidget(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/apps/from-widget",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete App
     * @param podId
     * @param appName
     * @returns AppMessageResponse Successful Response
     * @throws ApiError
     */
    static appDelete(podId, appName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/apps/{app_name}",
        path: {
          "pod_id": podId,
          "app_name": appName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get App
     * @param podId
     * @param appName
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    static appGet(podId, appName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/apps/{app_name}",
        path: {
          "pod_id": podId,
          "app_name": appName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update App
     * @param podId
     * @param appName
     * @param requestBody
     * @returns AppDetailResponse Successful Response
     * @throws ApiError
     */
    static appUpdate(podId, appName, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/apps/{app_name}",
        path: {
          "pod_id": podId,
          "app_name": appName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get App Root Asset
     * @param podId
     * @param appName
     * @returns any Successful Response
     * @throws ApiError
     */
    static appAssetRootGet(podId, appName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/apps/{app_name}/assets",
        path: {
          "pod_id": podId,
          "app_name": appName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get App Asset
     * @param podId
     * @param appName
     * @param assetPath
     * @returns any Successful Response
     * @throws ApiError
     */
    static appAssetGet(podId, appName, assetPath) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/apps/{app_name}/assets/{asset_path}",
        path: {
          "pod_id": podId,
          "app_name": appName,
          "asset_path": assetPath
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Upload App Bundle
     * @param podId
     * @param appName
     * @param formData
     * @returns AppBundleUploadResponse Successful Response
     * @throws ApiError
     */
    static appBundleUpload(podId, appName, formData) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/apps/{app_name}/bundle",
        path: {
          "pod_id": podId,
          "app_name": appName
        },
        formData,
        mediaType: "multipart/form-data",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Download App Dist Archive
     * @param podId
     * @param appName
     * @returns binary Zip archive bytes
     * @throws ApiError
     */
    static appDistArchiveGet(podId, appName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/apps/{app_name}/dist/archive",
        path: {
          "pod_id": podId,
          "app_name": appName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Download App Source Archive
     * @param podId
     * @param appName
     * @returns binary Zip archive bytes
     * @throws ApiError
     */
    static appSourceArchiveGet(podId, appName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/apps/{app_name}/source/archive",
        path: {
          "pod_id": podId,
          "app_name": appName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/apps.ts
  var AppsNamespace = class {
    constructor(client, http, podId) {
      __publicField(this, "client", client);
      __publicField(this, "http", http);
      __publicField(this, "podId", podId);
      __publicField(this, "assets", {
        get: (name, path) => this.http.request("GET", `/pods/${this.podId()}/apps/${name}/assets${path ? `/${path.replace(/^\/+/, "")}` : ""}`)
      });
      __publicField(this, "bundle", {
        upload: (name, payload) => this.client.request(() => AppsService.appBundleUpload(this.podId(), name, payload))
      });
      __publicField(this, "source", {
        download: (name) => this.http.requestBytes("GET", `/pods/${this.podId()}/apps/${name}/source/archive`)
      });
      __publicField(this, "dist", {
        download: (name) => this.http.requestBytes("GET", `/pods/${this.podId()}/apps/${name}/dist/archive`)
      });
    }
    list(options = {}) {
      return this.client.request(() => {
        var _a;
        return AppsService.appList(this.podId(), (_a = options.limit) != null ? _a : 100, options.pageToken);
      });
    }
    create(payload) {
      return this.client.request(() => AppsService.appCreate(this.podId(), payload));
    }
    get(name) {
      return this.client.request(() => AppsService.appGet(this.podId(), name));
    }
    update(name, payload) {
      return this.client.request(() => AppsService.appUpdate(this.podId(), name, payload));
    }
    delete(name) {
      return this.client.request(() => AppsService.appDelete(this.podId(), name));
    }
    /** Promote a conversation widget into a persisted app (save as app). */
    createFromWidget(payload) {
      return this.http.request("POST", `/pods/${this.podId()}/apps/from-widget`, {
        body: payload
      });
    }
  };

  // src/openapi_client/services/FilesService.ts
  var FilesService = class {
    /**
     * List Files
     * @param podId
     * @param directoryPath
     * @param limit
     * @param pageToken
     * @returns FileListResponse Successful Response
     * @throws ApiError
     */
    static fileList(podId, directoryPath = "/", limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/files",
        path: {
          "pod_id": podId
        },
        query: {
          "directory_path": directoryPath,
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Upload File
     * @param podId
     * @param formData
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    static fileUpload(podId, formData) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/files",
        path: {
          "pod_id": podId
        },
        formData,
        mediaType: "multipart/form-data",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete File Or Folder
     * @param podId
     * @param path
     * @returns void
     * @throws ApiError
     */
    static fileDelete(podId, path) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/datastore/files/by-path",
        path: {
          "pod_id": podId
        },
        query: {
          "path": path
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get File
     * @param podId
     * @param path
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    static fileGet(podId, path) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/files/by-path",
        path: {
          "pod_id": podId
        },
        query: {
          "path": path
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update File
     * @param podId
     * @param formData
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    static fileUpdate(podId, formData) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/datastore/files/by-path",
        path: {
          "pod_id": podId
        },
        formData,
        mediaType: "multipart/form-data",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List a document's derived child files
     * @param podId
     * @param path
     * @returns FileChildrenResponse Successful Response
     * @throws ApiError
     */
    static fileChildrenList(podId, path) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/files/children",
        path: {
          "pod_id": podId
        },
        query: {
          "path": path
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Fetch a document's child artifact by path
     * @param podId
     * @param path Child path, e.g. /folder/report.pdf/document.md, /folder/report.pdf/image_0.png, or /folder/report.pdf/pages/page_0001.jpg
     * @param pageStart
     * @param pageEnd
     * @returns binary File bytes
     * @throws ApiError
     */
    static fileChildGet(podId, path, pageStart, pageEnd) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/files/children/content",
        path: {
          "pod_id": podId
        },
        query: {
          "path": path,
          "page_start": pageStart,
          "page_end": pageEnd
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Download File
     * @param podId
     * @param path
     * @returns binary File bytes
     * @throws ApiError
     */
    static fileDownload(podId, path) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/files/download",
        path: {
          "pod_id": podId
        },
        query: {
          "path": path
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Folder
     * @param podId
     * @param requestBody
     * @returns FileDetailResponse Successful Response
     * @throws ApiError
     */
    static fileFolderCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/files/folders",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Search Files
     * @param podId
     * @param requestBody
     * @returns FileSearchResponse Successful Response
     * @throws ApiError
     */
    static fileSearch(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/files/search",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create a public, hit-capped signed URL for a file
     * @param podId
     * @param path
     * @param requestBody
     * @returns FileSignedUrlResponse Successful Response
     * @throws ApiError
     */
    static fileSignedUrl(podId, path, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/files/signed-url",
        path: {
          "pod_id": podId
        },
        query: {
          "path": path
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Directory Tree
     * @param podId
     * @param rootPath
     * @param filesPerDirectory
     * @returns DirectoryTreeResponse Successful Response
     * @throws ApiError
     */
    static fileTree(podId, rootPath = "/", filesPerDirectory = 3) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/files/tree",
        path: {
          "pod_id": podId
        },
        query: {
          "root_path": rootPath,
          "files_per_directory": filesPerDirectory
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get a short-lived URL for a file
     * @param podId
     * @param path
     * @returns FileUrlResponse Successful Response
     * @throws ApiError
     */
    static fileUrl(podId, path) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/files/url",
        path: {
          "pod_id": podId
        },
        query: {
          "path": path
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/files.ts
  function joinDatastorePath(basePath, leaf) {
    const normalizedLeaf = leaf.replace(/^\/+/, "");
    const trimmedBase = (basePath != null ? basePath : "/").trim();
    const normalizedBase = trimmedBase.length > 0 ? trimmedBase : "/";
    if (normalizedBase === "/") {
      return `/${normalizedLeaf}`;
    }
    return `${normalizedBase.replace(/\/+$/, "")}/${normalizedLeaf}`;
  }
  function getDirectoryPath(path) {
    const normalized = path.trim();
    if (!normalized || normalized === "/") {
      return "/";
    }
    const withoutTrailing = normalized.replace(/\/+$/, "");
    const index = withoutTrailing.lastIndexOf("/");
    if (index <= 0) {
      return "/";
    }
    return withoutTrailing.slice(0, index);
  }
  function getBaseName(path) {
    const normalized = path.trim().replace(/\/+$/, "");
    const index = normalized.lastIndexOf("/");
    if (index === -1) {
      return normalized;
    }
    return normalized.slice(index + 1);
  }
  var FilesNamespace = class {
    constructor(client, http, podId) {
      __publicField(this, "client", client);
      __publicField(this, "http", http);
      __publicField(this, "podId", podId);
      __publicField(this, "folder", {
        create: (name, options = {}) => {
          var _a;
          const payload = {
            path: joinDatastorePath((_a = options.directoryPath) != null ? _a : options.parentId, name),
            description: options.description
          };
          return this.client.request(() => FilesService.fileFolderCreate(this.podId(), payload));
        }
      });
      // Derived child files of a processed document (converted markdown, extracted
      // figures, and on-demand page renders), addressed by `<file-path>/<artifact>`.
      __publicField(this, "children", {
        list: (path) => this.client.request(() => FilesService.fileChildrenList(this.podId(), path)),
        content: (childPath, options = {}) => {
          const params = new URLSearchParams({ path: childPath });
          if (options.pageStart != null) params.set("page_start", String(options.pageStart));
          if (options.pageEnd != null) params.set("page_end", String(options.pageEnd));
          return this.http.requestBytes(
            "GET",
            `/pods/${this.podId()}/datastore/files/children/content?${params.toString()}`
          );
        },
        markdown: (path, options = {}) => this.children.content(`${path}/document.md`, options)
      });
    }
    list(options = {}) {
      var _a, _b;
      const directoryPath = (_b = (_a = options.directoryPath) != null ? _a : options.parentId) != null ? _b : "/";
      return this.client.request(() => {
        var _a2;
        return FilesService.fileList(
          this.podId(),
          directoryPath,
          (_a2 = options.limit) != null ? _a2 : 100,
          options.pageToken
        );
      });
    }
    get(path) {
      return this.client.request(() => FilesService.fileGet(this.podId(), path));
    }
    /**
     * URLs for a file: a short-lived download `url` plus a permanent
     * authenticated `app_url` deep-link that opens the file in the Lemma
     * frontend (the viewer must be a signed-in pod member).
     */
    getUrl(path) {
      return this.client.request(() => FilesService.fileUrl(this.podId(), path));
    }
    /**
     * Mint a public, hit-capped short signed URL (no login needed to open).
     * Expires after `expiresSeconds` (default 3h, max 24h) and serves the file
     * at most `maxHits` times (default 50, max 100); both bounds are clamped
     * server-side. Use it to share a file outside the pod without unbounded egress.
     */
    createSignedUrl(path, options = {}) {
      const body = {
        expires_seconds: options.expiresSeconds,
        max_hits: options.maxHits
      };
      return this.client.request(() => FilesService.fileSignedUrl(this.podId(), path, body));
    }
    delete(path) {
      return this.client.request(() => FilesService.fileDelete(this.podId(), path));
    }
    search(query, options = {}) {
      return this.client.request(() => {
        var _a, _b;
        return FilesService.fileSearch(this.podId(), {
          query,
          limit: (_a = options.limit) != null ? _a : 10,
          scope_mode: options.scopeMode,
          scope_path: options.scopePath,
          search_method: (_b = options.searchMethod) != null ? _b : "HYBRID" /* HYBRID */
        });
      });
    }
    download(path) {
      const encodedPath = encodeURIComponent(path);
      return this.http.requestBytes(
        "GET",
        `/pods/${this.podId()}/datastore/files/download?path=${encodedPath}`
      );
    }
    tree(options = {}) {
      return this.client.request(
        () => {
          var _a, _b;
          return FilesService.fileTree(
            this.podId(),
            (_a = options.rootPath) != null ? _a : "/",
            (_b = options.filesPerDirectory) != null ? _b : 3
          );
        }
      );
    }
    upload(file, options = {}) {
      var _a, _b, _c, _d;
      const payload = {
        data: file,
        name: (_a = options.name) != null ? _a : file instanceof File ? file.name : void 0,
        description: options.description,
        directory_path: (_c = (_b = options.directoryPath) != null ? _b : options.parentId) != null ? _c : "/",
        search_enabled: (_d = options.searchEnabled) != null ? _d : true
      };
      return this.client.request(() => FilesService.fileUpload(this.podId(), payload));
    }
    update(path, options = {}) {
      var _a, _b, _c;
      const targetDirectory = (_a = options.directoryPath) != null ? _a : options.parentId;
      const resolvedNewPath = (_c = (_b = options.newPath) != null ? _b : options.name ? joinDatastorePath(targetDirectory != null ? targetDirectory : getDirectoryPath(path), options.name) : void 0) != null ? _c : targetDirectory ? joinDatastorePath(targetDirectory, getBaseName(path)) : void 0;
      const payload = {
        path,
        data: options.file,
        description: options.description,
        new_path: resolvedNewPath,
        search_enabled: options.searchEnabled,
        visibility: options.visibility
      };
      return this.client.request(() => FilesService.fileUpdate(this.podId(), payload));
    }
  };

  // src/openapi_client/services/FunctionsService.ts
  var FunctionsService = class {
    /**
     * List Functions
     * List all functions in a pod
     * @param podId
     * @param limit
     * @param pageToken
     * @returns FunctionListResponse Successful Response
     * @throws ApiError
     */
    static functionList(podId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/functions",
        path: {
          "pod_id": podId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Function
     * Create a new function in a pod. Do not send input_schema, output_schema, or config_schema; the platform derives those schemas from the function code and returns them in the response.
     * @param podId
     * @param requestBody
     * @returns FunctionActionResponse Successful Response
     * @throws ApiError
     */
    static functionCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/functions",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Function
     * Delete a function
     * @param podId
     * @param functionName
     * @returns FunctionMessageResponse Successful Response
     * @throws ApiError
     */
    static functionDelete(podId, functionName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/functions/{function_name}",
        path: {
          "pod_id": podId,
          "function_name": functionName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Function
     * Get a function by name
     * @param podId
     * @param functionName
     * @returns FunctionDetailResponse Successful Response
     * @throws ApiError
     */
    static functionGet(podId, functionName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/functions/{function_name}",
        path: {
          "pod_id": podId,
          "function_name": functionName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Function
     * Update a function. When code is supplied, the platform re-derives the function input_schema and output_schema and returns the refreshed function.
     * @param podId
     * @param functionName
     * @param requestBody
     * @returns FunctionActionResponse Successful Response
     * @throws ApiError
     */
    static functionUpdate(podId, functionName, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/functions/{function_name}",
        path: {
          "pod_id": podId,
          "function_name": functionName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Function Resource Permissions
     * Get explicit resource grants assigned to a function.
     * @param podId
     * @param functionName
     * @returns FunctionPermissionsResponse Successful Response
     * @throws ApiError
     */
    static functionPermissionsGet(podId, functionName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/functions/{function_name}/permissions",
        path: {
          "pod_id": podId,
          "function_name": functionName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Replace Function Resource Permissions
     * Replace explicit resource grants assigned to a function.
     * @param podId
     * @param functionName
     * @param requestBody
     * @returns FunctionPermissionsResponse Successful Response
     * @throws ApiError
     */
    static functionPermissionsReplace(podId, functionName, requestBody) {
      return request(OpenAPI, {
        method: "PUT",
        url: "/pods/{pod_id}/functions/{function_name}/permissions",
        path: {
          "pod_id": podId,
          "function_name": functionName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Runs
     * List runs for a function
     * @param podId
     * @param functionName
     * @param limit
     * @param pageToken
     * @returns FunctionRunListResponse Successful Response
     * @throws ApiError
     */
    static functionRunList(podId, functionName, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/functions/{function_name}/runs",
        path: {
          "pod_id": podId,
          "function_name": functionName
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Execute Function
     * Execute a function
     * @param podId
     * @param functionName
     * @param requestBody
     * @returns FunctionRunResponse Successful Response
     * @throws ApiError
     */
    static functionRun(podId, functionName, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/functions/{function_name}/runs",
        path: {
          "pod_id": podId,
          "function_name": functionName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Run
     * Get a specific function run
     * @param podId
     * @param functionName
     * @param runId
     * @returns FunctionRunResponse Successful Response
     * @throws ApiError
     */
    static functionRunGet(podId, functionName, runId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/functions/{function_name}/runs/{run_id}",
        path: {
          "pod_id": podId,
          "function_name": functionName,
          "run_id": runId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/functions.ts
  var FunctionsNamespace = class {
    constructor(client, podId) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
      __publicField(this, "permissions", {
        get: (name) => this.client.request(() => FunctionsService.functionPermissionsGet(this.podId(), name)),
        replace: (name, payload) => this.client.request(() => FunctionsService.functionPermissionsReplace(this.podId(), name, payload))
      });
      __publicField(this, "runs", {
        create: (name, options = {}) => this.client.request(() => {
          const payload = { input_data: options.input };
          return FunctionsService.functionRun(this.podId(), name, payload);
        }),
        list: (name, params = {}) => this.client.request(() => {
          var _a;
          return FunctionsService.functionRunList(this.podId(), name, (_a = params.limit) != null ? _a : 100, params.pageToken);
        }),
        get: (name, runId) => this.client.request(() => FunctionsService.functionRunGet(this.podId(), name, runId))
      });
    }
    list(options = {}) {
      return this.client.request(() => {
        var _a;
        return FunctionsService.functionList(this.podId(), (_a = options.limit) != null ? _a : 100, options.pageToken);
      });
    }
    create(payload) {
      return this.client.request(() => FunctionsService.functionCreate(this.podId(), payload));
    }
    get(name) {
      return this.client.request(() => FunctionsService.functionGet(this.podId(), name));
    }
    update(name, payload) {
      return this.client.request(() => FunctionsService.functionUpdate(this.podId(), name, payload));
    }
    delete(name) {
      return this.client.request(() => FunctionsService.functionDelete(this.podId(), name));
    }
    /** Run a function — convenience alias for `functions.runs.create`, matching the
     *  Python SDK's `functions.run(name, input)` and the unified `.run` verb. */
    run(name, options = {}) {
      return this.runs.create(name, options);
    }
  };

  // src/openapi_client/services/IconsService.ts
  var IconsService = class {
    /**
     * Upload Icon
     * Upload an image asset and receive a public icon URL.
     * @param formData
     * @returns IconUploadResponse Successful Response
     * @throws ApiError
     */
    static iconUpload(formData) {
      return request(OpenAPI, {
        method: "POST",
        url: "/icons/upload",
        formData,
        mediaType: "multipart/form-data",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Public Icon
     * Fetch a previously uploaded public icon asset.
     * @param iconPath
     * @returns any Successful Response
     * @throws ApiError
     */
    static iconPublicGet(iconPath) {
      return request(OpenAPI, {
        method: "GET",
        url: "/public/icons/{icon_path}",
        path: {
          "icon_path": iconPath
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/icons.ts
  var IconsNamespace = class {
    constructor(client) {
      __publicField(this, "client", client);
    }
    upload(file) {
      const payload = {
        file
      };
      return this.client.request(() => IconsService.iconUpload(payload));
    }
    getPublic(iconPath) {
      return this.client.request(() => IconsService.iconPublicGet(iconPath));
    }
  };

  // src/openapi_client/services/ConnectorsService.ts
  var ConnectorsService = class {
    /**
     * List Connectors
     * Get all active connectors available for connector
     * @param limit
     * @param pageToken
     * @returns ConnectorListResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorList(limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/connectors",
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * OAuth Callback
     * Handle OAuth callback and complete account connection. This endpoint is public and uses state parameter for security.
     * @param error
     * @param format
     * @returns string Successful Response
     * @throws ApiError
     */
    static connectorOauthCallback(error, format) {
      return request(OpenAPI, {
        method: "GET",
        url: "/connectors/connect-requests/oauth/callback",
        query: {
          "error": error,
          "format": format
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Connector
     * Get a specific connector by ID along with its operation catalog
     * @param connectorId
     * @returns ConnectorDetailResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorGet(connectorId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/connectors/{connector_id}",
        path: {
          "connector_id": connectorId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Connector Skill
     * Get the skill guide markdown for a connector. Pass `provider=lemma` or `provider=composio` to get provider-specific instructions when the app supports both. Falls back to the generic doc if no provider-specific file exists. Returns 404 if no skill doc has been generated yet.
     * @param connectorId
     * @param provider Provider override: lemma or composio
     * @returns ConnectorSkillResponse Successful Response
     * @throws ApiError
     */
    static connectorSkillGet(connectorId, provider) {
      return request(OpenAPI, {
        method: "GET",
        url: "/connectors/{connector_id}/skill",
        path: {
          "connector_id": connectorId
        },
        query: {
          "provider": provider
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Accounts
     * Get all connected accounts for the current user. Optionally filter by connector_id or connector_name
     * @param organizationId
     * @param connectorId
     * @param limit
     * @param pageToken
     * @returns AccountListResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAccountList(organizationId, connectorId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/accounts",
        path: {
          "organization_id": organizationId
        },
        query: {
          "connector_id": connectorId,
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Account
     * Directly connect a credential-managed native account for an org auth config.
     * @param organizationId
     * @param requestBody
     * @returns AccountResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAccountCreate(organizationId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{organization_id}/connectors/accounts",
        path: {
          "organization_id": organizationId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Account
     * Delete a connected account and revoke the connection
     * @param organizationId
     * @param accountId
     * @returns MessageResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAccountDelete(organizationId, accountId) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/organizations/{organization_id}/connectors/accounts/{account_id}",
        path: {
          "organization_id": organizationId,
          "account_id": accountId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Account
     * Get a specific account by ID
     * @param organizationId
     * @param accountId
     * @returns AccountResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAccountGet(organizationId, accountId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/accounts/{account_id}",
        path: {
          "organization_id": organizationId,
          "account_id": accountId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Credentials
     * Get the credentials for a specific account
     * @param organizationId
     * @param accountId
     * @returns AccountCredentialsResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAccountCredentialsGet(organizationId, accountId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/accounts/{account_id}/credentials",
        path: {
          "organization_id": organizationId,
          "account_id": accountId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Auth Configs
     * @param organizationId
     * @param limit
     * @param pageToken
     * @returns AuthConfigListResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAuthConfigList(organizationId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/auth-configs",
        path: {
          "organization_id": organizationId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Auth Config
     * @param organizationId
     * @param requestBody
     * @returns AuthConfigResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAuthConfigCreate(organizationId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{organization_id}/connectors/auth-configs",
        path: {
          "organization_id": organizationId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Auth Config
     * @param organizationId
     * @param authConfigName
     * @returns boolean Successful Response
     * @throws ApiError
     */
    static connectorAuthConfigDelete(organizationId, authConfigName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Auth Config
     * @param organizationId
     * @param authConfigName
     * @returns AuthConfigResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorAuthConfigGet(organizationId, authConfigName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Initiate Connect Request
     * Initiate an OAuth connection request for a connector
     * @param organizationId
     * @param requestBody
     * @returns ConnectRequestResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorConnectRequestCreate(organizationId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{organization_id}/connectors/connect-requests",
        path: {
          "organization_id": organizationId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Connector Status
     * @param organizationId
     * @returns ConnectorStatusResponse Successful Response
     * @throws ApiError
     */
    static connectorStatusGet(organizationId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/status",
        path: {
          "organization_id": organizationId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Discover Connector Operations
     * @param organizationId
     * @param authConfigName
     * @param query
     * @param limit
     * @returns OperationDiscoverResponse Successful Response
     * @throws ApiError
     */
    static connectorOperationDiscover(organizationId, authConfigName, query, limit = 100) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/{auth_config_name}/operations",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName
        },
        query: {
          "query": query,
          "limit": limit
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Connector Operation Details In Batch
     * @param organizationId
     * @param authConfigName
     * @param requestBody
     * @returns OperationDetailsBatchResponse Successful Response
     * @throws ApiError
     */
    static connectorOperationDetailsBatch(organizationId, authConfigName, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{organization_id}/connectors/{auth_config_name}/operations/details",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Connector Operation Details
     * @param organizationId
     * @param authConfigName
     * @param operationName
     * @returns OperationDetail Successful Response
     * @throws ApiError
     */
    static connectorOperationDetail(organizationId, authConfigName, operationName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName,
          "operation_name": operationName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Execute Connector Operation
     * @param organizationId
     * @param authConfigName
     * @param operationName
     * @param requestBody
     * @returns OperationExecutionResponse Successful Response
     * @throws ApiError
     */
    static connectorOperationExecute(organizationId, authConfigName, operationName, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}/execute",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName,
          "operation_name": operationName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Connector Triggers
     * @param organizationId
     * @param authConfigName
     * @param search
     * @param limit
     * @returns AppTriggerListResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorTriggerList(organizationId, authConfigName, search, limit = 100) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/{auth_config_name}/triggers",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName
        },
        query: {
          "search": search,
          "limit": limit
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Connector Trigger
     * @param organizationId
     * @param authConfigName
     * @param triggerName
     * @returns AppTriggerResponseSchema Successful Response
     * @throws ApiError
     */
    static connectorTriggerGet(organizationId, authConfigName, triggerName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{organization_id}/connectors/{auth_config_name}/triggers/{trigger_name}",
        path: {
          "organization_id": organizationId,
          "auth_config_name": authConfigName,
          "trigger_name": triggerName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/connectors.ts
  function encodePath(value) {
    return encodeURIComponent(value);
  }
  var ConnectorsNamespace = class {
    constructor(client, http) {
      __publicField(this, "client", client);
      __publicField(this, "http", http);
      __publicField(this, "operations", {
        discover: (scope, options = {}) => this.client.request(() => {
          var _a;
          return ConnectorsService.connectorOperationDiscover(
            scope.organizationId,
            scope.authConfigName,
            options.query,
            (_a = options.limit) != null ? _a : 100
          );
        }),
        list: async (scope, options = {}) => {
          var _a;
          const response = await this.client.request(() => {
            var _a2;
            return ConnectorsService.connectorOperationDiscover(
              scope.organizationId,
              scope.authConfigName,
              options.query,
              (_a2 = options.limit) != null ? _a2 : 100
            );
          });
          return (_a = response.items) != null ? _a : [];
        },
        get: (scope, operationName) => this.client.request(() => ConnectorsService.connectorOperationDetail(
          scope.organizationId,
          scope.authConfigName,
          operationName
        )),
        details: (scope, operationNames) => {
          const body = { operation_names: operationNames };
          return this.client.request(() => ConnectorsService.connectorOperationDetailsBatch(
            scope.organizationId,
            scope.authConfigName,
            body
          ));
        },
        execute: (scope, operationName, payload, accountId) => {
          const body = { payload, account_id: accountId };
          return this.client.request(() => ConnectorsService.connectorOperationExecute(
            scope.organizationId,
            scope.authConfigName,
            operationName,
            body
          ));
        }
      });
      __publicField(this, "triggers", {
        list: (scope, options = {}) => this.client.request(() => {
          var _a;
          return ConnectorsService.connectorTriggerList(
            scope.organizationId,
            scope.authConfigName,
            options.search,
            (_a = options.limit) != null ? _a : 100
          );
        }),
        get: (scope, triggerName) => this.client.request(() => ConnectorsService.connectorTriggerGet(
          scope.organizationId,
          scope.authConfigName,
          triggerName
        ))
      });
      __publicField(this, "accounts", {
        list: (organizationId, options = {}) => this.client.request(() => {
          var _a;
          return ConnectorsService.connectorAccountList(
            organizationId,
            options.connectorId,
            (_a = options.limit) != null ? _a : 100,
            options.pageToken
          );
        }),
        create: (organizationId, payload) => this.client.request(() => ConnectorsService.connectorAccountCreate(
          organizationId,
          payload
        )),
        get: (organizationId, accountId) => this.client.request(() => ConnectorsService.connectorAccountGet(
          organizationId,
          accountId
        )),
        credentials: (organizationId, accountId) => this.client.request(() => ConnectorsService.connectorAccountCredentialsGet(
          organizationId,
          accountId
        )),
        delete: (organizationId, accountId) => this.client.request(() => ConnectorsService.connectorAccountDelete(
          organizationId,
          accountId
        )),
        /**
         * @deprecated Use list/get/create with an organization id. Kept only for
         * callers that still need the response shape while migrating.
         */
        listOrgScoped: (organizationId, options = {}) => {
          var _a;
          return this.http.request(
            "GET",
            `/organizations/${encodePath(organizationId)}/connectors/accounts`,
            {
              params: {
                connector_id: options.connectorId,
                limit: (_a = options.limit) != null ? _a : 100,
                page_token: options.pageToken
              }
            }
          );
        }
      });
      __publicField(this, "authConfigs", {
        list: (organizationId, options = {}) => this.client.request(() => {
          var _a;
          return ConnectorsService.connectorAuthConfigList(
            organizationId,
            (_a = options.limit) != null ? _a : 100,
            options.pageToken
          );
        }),
        create: (organizationId, payload) => this.client.request(() => ConnectorsService.connectorAuthConfigCreate(
          organizationId,
          payload
        )),
        get: (organizationId, authConfigName) => this.client.request(() => ConnectorsService.connectorAuthConfigGet(
          organizationId,
          authConfigName
        )),
        delete: (organizationId, authConfigName) => this.client.request(() => ConnectorsService.connectorAuthConfigDelete(
          organizationId,
          authConfigName
        ))
      });
    }
    list(options = {}) {
      return this.client.request(() => {
        var _a;
        return ConnectorsService.connectorList((_a = options.limit) != null ? _a : 100, options.pageToken);
      });
    }
    get(connectorId) {
      return this.client.request(() => ConnectorsService.connectorGet(connectorId));
    }
    async enableApp(organizationId, connectorId, options = {}) {
      var _a, _b;
      const configs = await this.authConfigs.list(organizationId, { limit: 100 });
      const existing = configs.items.find((config) => config.connector_id === connectorId && config.status === "ACTIVE");
      if (existing) return existing;
      return this.authConfigs.create(organizationId, {
        connector_id: connectorId,
        provider: options.provider,
        config_source: (_a = options.config_source) != null ? _a : "SYSTEM_DEFAULT",
        credential_config: (_b = options.credential_config) != null ? _b : options.provider_config,
        name: options.name
      });
    }
    createConnectRequest(organizationId, input) {
      const payload = typeof input === "string" ? { connector_id: input } : input;
      return this.client.request(() => ConnectorsService.connectorConnectRequestCreate(organizationId, payload));
    }
  };

  // src/openapi_client/services/OrganizationsService.ts
  var OrganizationsService = class {
    /**
     * List My Organizations
     * Get all organizations the current user belongs to
     * @param limit
     * @param pageToken
     * @returns OrganizationListResponse Successful Response
     * @throws ApiError
     */
    static orgList(limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations",
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Organization
     * Create a new organization
     * @param requestBody
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    static orgCreate(requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations",
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List My Invitations
     * Get all pending invitations for the current user
     * @param status
     * @param limit
     * @param pageToken
     * @returns OrganizationInvitationListResponse Successful Response
     * @throws ApiError
     */
    static orgInvitationListMine(status = "PENDING" /* PENDING */, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/invitations",
        query: {
          "status": status,
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Revoke Invitation
     * Revoke an organization invitation
     * @param invitationId
     * @returns void
     * @throws ApiError
     */
    static orgInvitationRevoke(invitationId) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/organizations/invitations/{invitation_id}",
        path: {
          "invitation_id": invitationId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Organization Invitation
     * Get an invitation by id
     * @param invitationId
     * @returns OrganizationInvitationResponse Successful Response
     * @throws ApiError
     */
    static orgInvitationGet(invitationId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/invitations/{invitation_id}",
        path: {
          "invitation_id": invitationId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Accept Invitation
     * Accept an organization invitation
     * @param invitationId
     * @returns OrganizationMessageResponse Successful Response
     * @throws ApiError
     */
    static orgInvitationAccept(invitationId) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/invitations/{invitation_id}/accept",
        path: {
          "invitation_id": invitationId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Check Organization Slug Availability
     * Check whether an organization slug is available
     * @param slug
     * @returns OrganizationSlugAvailabilityResponse Successful Response
     * @throws ApiError
     */
    static orgSlugAvailability(slug) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/slug-availability",
        query: {
          "slug": slug
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Suggested Organizations
     * Get auto-join organizations matching the current user's email domain
     * @param limit
     * @param pageToken
     * @returns OrganizationListResponse Successful Response
     * @throws ApiError
     */
    static orgSuggested(limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/suggested",
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Organization
     * Get organization details
     * @param orgId
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    static orgGet(orgId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{org_id}",
        path: {
          "org_id": orgId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Organization
     * Update an organization's name or join policy (owner only)
     * @param orgId
     * @param requestBody
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    static orgUpdate(orgId, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/organizations/{org_id}",
        path: {
          "org_id": orgId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Organization Invitations
     * Get all pending invitations for an organization
     * @param orgId
     * @param status
     * @param limit
     * @param pageToken
     * @returns OrganizationInvitationListResponse Successful Response
     * @throws ApiError
     */
    static orgInvitationList(orgId, status = "PENDING" /* PENDING */, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{org_id}/invitations",
        path: {
          "org_id": orgId
        },
        query: {
          "status": status,
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Invite Member
     * Invite a user to join the organization
     * @param orgId
     * @param requestBody
     * @returns OrganizationInvitationResponse Successful Response
     * @throws ApiError
     */
    static orgInvitationInvite(orgId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{org_id}/invitations",
        path: {
          "org_id": orgId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Join Auto-Join Organization
     * Join an organization when the current user's email domain is allowed to auto-join
     * @param orgId
     * @returns OrganizationResponse Successful Response
     * @throws ApiError
     */
    static orgJoinAutoJoin(orgId) {
      return request(OpenAPI, {
        method: "POST",
        url: "/organizations/{org_id}/join",
        path: {
          "org_id": orgId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Organization Members
     * Get all members of an organization
     * @param orgId
     * @param limit
     * @param pageToken
     * @returns OrganizationMemberListResponse Successful Response
     * @throws ApiError
     */
    static orgMemberList(orgId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/organizations/{org_id}/members",
        path: {
          "org_id": orgId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Remove Member
     * Remove a member from the organization
     * @param orgId
     * @param memberId
     * @returns void
     * @throws ApiError
     */
    static orgMemberRemove(orgId, memberId) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/organizations/{org_id}/members/{member_id}",
        path: {
          "org_id": orgId,
          "member_id": memberId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Member Role
     * Update a member's role in the organization
     * @param orgId
     * @param memberId
     * @param requestBody
     * @returns OrganizationMemberResponse Successful Response
     * @throws ApiError
     */
    static orgMemberUpdateRole(orgId, memberId, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/organizations/{org_id}/members/{member_id}/role",
        path: {
          "org_id": orgId,
          "member_id": memberId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/organizations.ts
  var OrganizationsNamespace = class {
    constructor(client, http) {
      __publicField(this, "client", client);
      __publicField(this, "http", http);
      __publicField(this, "members", {
        list: (orgId, options = {}) => this.client.request(
          () => {
            var _a, _b;
            return OrganizationsService.orgMemberList(orgId, (_a = options.limit) != null ? _a : 100, (_b = options.pageToken) != null ? _b : options.cursor);
          }
        ),
        updateRole: (orgId, memberId, role) => this.client.request(
          () => OrganizationsService.orgMemberUpdateRole(orgId, memberId, { role })
        ),
        remove: (orgId, memberId) => this.client.request(() => OrganizationsService.orgMemberRemove(orgId, memberId))
      });
      __publicField(this, "invitations", {
        listMine: async (options = {}) => {
          var _a, _b;
          if (options.status) {
            return this.client.request(
              () => {
                var _a2, _b2;
                return OrganizationsService.orgInvitationListMine(
                  options.status,
                  (_a2 = options.limit) != null ? _a2 : 100,
                  (_b2 = options.pageToken) != null ? _b2 : options.cursor
                );
              }
            );
          }
          return this.http.request("GET", "/organizations/invitations", {
            params: {
              limit: (_a = options.limit) != null ? _a : 100,
              page_token: (_b = options.pageToken) != null ? _b : options.cursor
            }
          });
        },
        list: async (orgId, options = {}) => {
          var _a, _b;
          if (options.status) {
            return this.client.request(
              () => {
                var _a2, _b2;
                return OrganizationsService.orgInvitationList(
                  orgId,
                  options.status,
                  (_a2 = options.limit) != null ? _a2 : 100,
                  (_b2 = options.pageToken) != null ? _b2 : options.cursor
                );
              }
            );
          }
          return this.http.request(
            "GET",
            `/organizations/${encodeURIComponent(orgId)}/invitations`,
            {
              params: {
                limit: (_a = options.limit) != null ? _a : 100,
                page_token: (_b = options.pageToken) != null ? _b : options.cursor
              }
            }
          );
        },
        get: (invitationId) => this.client.request(() => OrganizationsService.orgInvitationGet(invitationId)),
        invite: (orgId, payload) => this.client.request(() => OrganizationsService.orgInvitationInvite(orgId, payload)),
        accept: (invitationId) => this.client.request(() => OrganizationsService.orgInvitationAccept(invitationId)),
        revoke: (invitationId) => this.client.request(() => OrganizationsService.orgInvitationRevoke(invitationId))
      });
    }
    list(options = {}) {
      return this.client.request(
        () => {
          var _a, _b;
          return OrganizationsService.orgList((_a = options.limit) != null ? _a : 100, (_b = options.pageToken) != null ? _b : options.cursor);
        }
      );
    }
    get(orgId) {
      return this.client.request(() => OrganizationsService.orgGet(orgId));
    }
    create(payload) {
      return this.client.request(() => OrganizationsService.orgCreate(payload));
    }
  };

  // src/openapi_client/services/PodMembersService.ts
  var PodMembersService = class {
    /**
     * List Pod Members
     * List all members of a pod
     * @param podId
     * @param limit
     * @param pageToken
     * @returns PodMemberListResponse Successful Response
     * @throws ApiError
     */
    static podMemberList(podId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/members",
        path: {
          "pod_id": podId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Add Pod Member
     * Add a member to a pod
     * @param podId
     * @param requestBody
     * @returns PodMemberResponse Successful Response
     * @throws ApiError
     */
    static podMemberAdd(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/members",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Lookup Pod Member By Email
     * Resolve a pod member by email
     * @param podId
     * @param email
     * @returns PodMemberDetailResponse Successful Response
     * @throws ApiError
     */
    static podMemberLookupByEmail(podId, email) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/members/lookup/by-email",
        path: {
          "pod_id": podId
        },
        query: {
          "email": email
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Lookup Pod Member By User ID
     * Resolve a pod member by user id
     * @param podId
     * @param userId
     * @returns PodMemberDetailResponse Successful Response
     * @throws ApiError
     */
    static podMemberLookupByUserId(podId, userId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/members/lookup/by-user-id/{user_id}",
        path: {
          "pod_id": podId,
          "user_id": userId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Remove Pod Member
     * Remove a member from a pod
     * @param podId
     * @param podMemberId
     * @returns void
     * @throws ApiError
     */
    static podMemberRemove(podId, podMemberId) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/members/{pod_member_id}",
        path: {
          "pod_id": podId,
          "pod_member_id": podMemberId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Pod Member
     * Get a pod member by pod member id
     * @param podId
     * @param podMemberId
     * @returns PodMemberDetailResponse Successful Response
     * @throws ApiError
     */
    static podMemberGet(podId, podMemberId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/members/{pod_member_id}",
        path: {
          "pod_id": podId,
          "pod_member_id": podMemberId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Member Roles
     * Update a pod member's roles
     * @param podId
     * @param podMemberId
     * @param requestBody
     * @returns PodMemberResponse Successful Response
     * @throws ApiError
     */
    static podMemberUpdateRoles(podId, podMemberId, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/members/{pod_member_id}/roles",
        path: {
          "pod_id": podId,
          "pod_member_id": podMemberId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/pod-members.ts
  var PodMembersNamespace = class {
    constructor(client) {
      __publicField(this, "client", client);
    }
    list(podId, options = {}) {
      return this.client.request(
        () => {
          var _a, _b;
          return PodMembersService.podMemberList(podId, (_a = options.limit) != null ? _a : 100, (_b = options.pageToken) != null ? _b : options.cursor);
        }
      );
    }
    add(podId, payload) {
      return this.client.request(() => PodMembersService.podMemberAdd(podId, payload));
    }
    get(podId, podMemberId) {
      return this.client.request(() => PodMembersService.podMemberGet(podId, podMemberId));
    }
    lookupByEmail(podId, email) {
      return this.client.request(() => PodMembersService.podMemberLookupByEmail(podId, email));
    }
    lookupByUserId(podId, userId) {
      return this.client.request(() => PodMembersService.podMemberLookupByUserId(podId, userId));
    }
    updateRole(podId, podMemberId, role) {
      return this.client.request(
        () => PodMembersService.podMemberUpdateRoles(podId, podMemberId, { roles: [role] })
      );
    }
    updateRoles(podId, podMemberId, roles) {
      return this.client.request(
        () => PodMembersService.podMemberUpdateRoles(podId, podMemberId, { roles })
      );
    }
    remove(podId, podMemberId) {
      return this.client.request(() => PodMembersService.podMemberRemove(podId, podMemberId));
    }
  };

  // src/openapi_client/services/PodPermissionsService.ts
  var PodPermissionsService = class {
    /**
     * Get Pod Permission Catalog
     * @param podId
     * @returns PodPermissionCatalogResponse Successful Response
     * @throws ApiError
     */
    static podPermissionsCatalog(podId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/permissions/catalog",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get My Pod Permissions
     * @param podId
     * @returns PodEffectivePermissionsResponse Successful Response
     * @throws ApiError
     */
    static podPermissionsMe(podId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/permissions/me",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/pod-permissions.ts
  var PodPermissionsNamespace = class {
    constructor(client, http, podId) {
      __publicField(this, "client", client);
      __publicField(this, "http", http);
      __publicField(this, "podId", podId);
    }
    catalog(podId) {
      return this.client.request(() => PodPermissionsService.podPermissionsCatalog(podId != null ? podId : this.podId()));
    }
    me(podId) {
      const targetPodId = encodeURIComponent(podId != null ? podId : this.podId());
      return this.http.request("GET", `/pods/${targetPodId}/permissions/me`);
    }
  };

  // src/openapi_client/services/PodJoinRequestsService.ts
  var PodJoinRequestsService = class {
    /**
     * List Pod Join Requests
     * List join requests for a pod
     * @param podId
     * @param statusFilter
     * @param limit
     * @param pageToken
     * @returns PodJoinRequestListResponse Successful Response
     * @throws ApiError
     */
    static podJoinRequestList(podId, statusFilter, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/join-requests",
        path: {
          "pod_id": podId
        },
        query: {
          "status_filter": statusFilter,
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Pod Join Request
     * Create a join request for the current user to access this pod
     * @param podId
     * @returns PodJoinRequestCreateResponse Successful Response
     * @throws ApiError
     */
    static podJoinRequestCreate(podId) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/join-requests",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get My Pod Join Request
     * Get the current user's pending join request for this pod
     * @param podId
     * @returns any Successful Response
     * @throws ApiError
     */
    static podJoinRequestMe(podId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/join-requests/me",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Approve Pod Join Request
     * Approve a pending pod join request and add user to org/pod
     * @param podId
     * @param joinRequestId
     * @param requestBody
     * @returns PodJoinRequestCreateResponse Successful Response
     * @throws ApiError
     */
    static podJoinRequestApprove(podId, joinRequestId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/join-requests/{join_request_id}/approve",
        path: {
          "pod_id": podId,
          "join_request_id": joinRequestId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/pod-join-requests.ts
  var PodJoinRequestsNamespace = class {
    constructor(client) {
      __publicField(this, "client", client);
    }
    create(podId) {
      return this.client.request(() => PodJoinRequestsService.podJoinRequestCreate(podId));
    }
    me(podId) {
      return this.client.request(() => PodJoinRequestsService.podJoinRequestMe(podId));
    }
    list(podId, options = {}) {
      return this.client.request(
        () => {
          var _a, _b;
          return PodJoinRequestsService.podJoinRequestList(
            podId,
            options.status,
            (_a = options.limit) != null ? _a : 100,
            (_b = options.pageToken) != null ? _b : options.cursor
          );
        }
      );
    }
    approve(podId, joinRequestId, payload = {}) {
      return this.client.request(
        () => {
          var _a, _b;
          return PodJoinRequestsService.podJoinRequestApprove(podId, joinRequestId, {
            org_role: (_a = payload.org_role) != null ? _a : "ORG_MEMBER" /* ORG_MEMBER */,
            pod_role: (_b = payload.pod_role) != null ? _b : "POD_USER" /* POD_USER */
          });
        }
      );
    }
  };

  // src/openapi_client/services/PodsService.ts
  var PodsService = class {
    /**
     * Create Pod
     * Create a new pod
     * @param requestBody
     * @returns PodResponse Successful Response
     * @throws ApiError
     */
    static podCreate(requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods",
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List PodS by Organization
     * List all pods in an organization
     * @param organizationId
     * @param limit
     * @param pageToken
     * @returns PodListResponse Successful Response
     * @throws ApiError
     */
    static podList(organizationId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/organization/{organization_id}",
        path: {
          "organization_id": organizationId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Pod
     * Delete a pod
     * @param podId
     * @returns void
     * @throws ApiError
     */
    static podDelete(podId) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Pod
     * Get pod details
     * @param podId
     * @returns PodResponse Successful Response
     * @throws ApiError
     */
    static podGet(podId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Pod
     * Update pod details
     * @param podId
     * @param requestBody
     * @returns PodResponse Successful Response
     * @throws ApiError
     */
    static podUpdate(podId, requestBody) {
      return request(OpenAPI, {
        method: "PUT",
        url: "/pods/{pod_id}",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Join Pod
     * Self-join a pod when its join policy (ORG_MEMBERS / PUBLIC) allows it
     * @param podId
     * @returns PodMemberResponse Successful Response
     * @throws ApiError
     */
    static podJoin(podId) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/join",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/pods.ts
  var PodsNamespace = class {
    constructor(client, http) {
      __publicField(this, "client", client);
      __publicField(this, "http", http);
    }
    list(options = {}) {
      var _a;
      if (options.organizationId) {
        return this.listByOrganization(options.organizationId, {
          limit: options.limit,
          pageToken: (_a = options.pageToken) != null ? _a : options.cursor
        });
      }
      throw new Error("organizationId is required for pods.list(). Use listByOrganization(organizationId).");
    }
    listByOrganization(organizationId, options = {}) {
      return this.client.request(
        () => {
          var _a, _b;
          return PodsService.podList(organizationId, (_a = options.limit) != null ? _a : 100, (_b = options.pageToken) != null ? _b : options.cursor);
        }
      );
    }
    get(podId) {
      return this.client.request(() => PodsService.podGet(podId));
    }
    create(payload) {
      return this.client.request(() => PodsService.podCreate(payload));
    }
    update(podId, payload) {
      return this.client.request(() => PodsService.podUpdate(podId, payload));
    }
    delete(podId) {
      return this.client.request(() => PodsService.podDelete(podId));
    }
  };

  // src/openapi_client/services/PodRolesService.ts
  var PodRolesService = class {
    /**
     * List Pod Roles
     * @param podId
     * @returns PodRoleListResponse Successful Response
     * @throws ApiError
     */
    static podRolesList(podId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/roles",
        path: {
          "pod_id": podId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Pod Role
     * @param podId
     * @param requestBody
     * @returns PodRoleResponse Successful Response
     * @throws ApiError
     */
    static podRolesCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/roles",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Pod Role
     * @param podId
     * @param roleName
     * @returns void
     * @throws ApiError
     */
    static podRolesDelete(podId, roleName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/roles/{role_name}",
        path: {
          "pod_id": podId,
          "role_name": roleName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Pod Role
     * @param podId
     * @param roleName
     * @param requestBody
     * @returns PodRoleResponse Successful Response
     * @throws ApiError
     */
    static podRolesUpdate(podId, roleName, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/roles/{role_name}",
        path: {
          "pod_id": podId,
          "role_name": roleName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Pod Role Permissions
     * @param podId
     * @param roleName
     * @returns PodRolePermissionsResponse Successful Response
     * @throws ApiError
     */
    static podRolePermissionsGet(podId, roleName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/roles/{role_name}/permissions",
        path: {
          "pod_id": podId,
          "role_name": roleName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Replace Pod Role Permissions
     * @param podId
     * @param roleName
     * @param requestBody
     * @returns PodRolePermissionsResponse Successful Response
     * @throws ApiError
     */
    static podRolePermissionsReplace(podId, roleName, requestBody) {
      return request(OpenAPI, {
        method: "PUT",
        url: "/pods/{pod_id}/roles/{role_name}/permissions",
        path: {
          "pod_id": podId,
          "role_name": roleName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/pod-roles.ts
  var PodRolesNamespace = class {
    constructor(client, podId) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
      __publicField(this, "permissions", {
        get: (roleName, podId) => this.client.request(() => PodRolesService.podRolePermissionsGet(podId != null ? podId : this.podId(), roleName)),
        replace: (roleName, payload, podId) => this.client.request(
          () => PodRolesService.podRolePermissionsReplace(podId != null ? podId : this.podId(), roleName, payload)
        )
      });
    }
    list(podId) {
      return this.client.request(() => PodRolesService.podRolesList(podId != null ? podId : this.podId()));
    }
    create(payload, podId) {
      return this.client.request(() => PodRolesService.podRolesCreate(podId != null ? podId : this.podId(), payload));
    }
    update(roleName, payload, podId) {
      return this.client.request(() => PodRolesService.podRolesUpdate(podId != null ? podId : this.podId(), roleName, payload));
    }
    delete(roleName, podId) {
      return this.client.request(() => PodRolesService.podRolesDelete(podId != null ? podId : this.podId(), roleName));
    }
  };

  // src/openapi_client/services/AgentSurfacesService.ts
  var AgentSurfacesService = class {
    /**
     * List Surfaces
     * @param podId
     * @param limit
     * @param pageToken
     * @returns AgentSurfaceListResponse Successful Response
     * @throws ApiError
     */
    static agentSurfaceList(podId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/surfaces",
        path: {
          "pod_id": podId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Surface
     * @param podId
     * @param platform
     * @returns void
     * @throws ApiError
     */
    static agentSurfaceDelete(podId, platform) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/surfaces/{platform}",
        path: {
          "pod_id": podId,
          "platform": platform
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Surface
     * @param podId
     * @param platform
     * @returns any Successful Response
     * @throws ApiError
     */
    static agentSurfaceGet(podId, platform) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/surfaces/{platform}",
        path: {
          "pod_id": podId,
          "platform": platform
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Upsert Surface
     * Create the surface for a platform, or merge updates into the existing one.
     *
     * A surface is unique per ``pod_id + platform``, so this single idempotent
     * write covers create, config edits, channel routing, account/credential
     * changes, and enable/disable. Only fields present in the request are applied
     * on update.
     * @param podId
     * @param platform
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    static agentSurfaceUpsert(podId, platform, requestBody) {
      return request(OpenAPI, {
        method: "PUT",
        url: "/pods/{pod_id}/surfaces/{platform}",
        path: {
          "pod_id": podId,
          "platform": platform
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Surface Channels
     * List the channels/groups this surface bot can be configured to respond in.
     *
     * Returns an empty list for platforms without an enumerable channel concept
     * (Telegram groups, WhatsApp, email).
     * @param podId
     * @param platform
     * @returns AvailableSurfaceChannelsResponse Successful Response
     * @throws ApiError
     */
    static agentSurfaceChannels(podId, platform) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/surfaces/{platform}/channels",
        path: {
          "pod_id": podId,
          "platform": platform
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Surface Setup
     * Everything needed to finish setting up this platform's surface.
     *
     * Merges the static platform checklist with live webhook + admin-consent
     * state. Works before the surface exists (guide only) and after (live state).
     * @param podId
     * @param platform
     * @returns SurfaceSetupResponse Successful Response
     * @throws ApiError
     */
    static agentSurfaceSetup(podId, platform) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/surfaces/{platform}/setup",
        path: {
          "pod_id": podId,
          "platform": platform
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/pod-surfaces.ts
  var PodSurfacesNamespace = class {
    constructor(client) {
      __publicField(this, "client", client);
    }
    list(podId, options = {}) {
      return this.client.request(
        () => {
          var _a, _b;
          return AgentSurfacesService.agentSurfaceList(
            podId,
            (_a = options.limit) != null ? _a : 100,
            (_b = options.pageToken) != null ? _b : options.cursor
          );
        }
      );
    }
    upsert(podId, platform, payload) {
      return this.client.request(
        () => AgentSurfacesService.agentSurfaceUpsert(podId, platform, payload)
      );
    }
    get(podId, platform) {
      return this.client.request(() => AgentSurfacesService.agentSurfaceGet(podId, platform));
    }
    delete(podId, platform) {
      return this.client.request(() => AgentSurfacesService.agentSurfaceDelete(podId, platform));
    }
    setup(podId, platform) {
      return this.client.request(() => AgentSurfacesService.agentSurfaceSetup(podId, platform));
    }
    channels(podId, platform) {
      return this.client.request(
        () => AgentSurfacesService.agentSurfaceChannels(podId, platform)
      );
    }
  };

  // src/openapi_client/services/RecordsService.ts
  var RecordsService = class {
    /**
     * List Records
     * List table records with token pagination only. Use the datastore query endpoint for joins, aggregates, or custom read-only SQL.
     * @param podId
     * @param tableName
     * @param limit Max number of rows to return.
     * @param offset Row offset for direct pagination.
     * @param filter Optional repeated JSON filters for advanced comparisons. Each `filter` value must be a JSON object with shape `{"field":"<column_name>","op":"<operator>","value":<comparison_value>}`. Allowed operators are: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`. Repeat the query parameter to combine multiple filters with AND semantics. Examples: `filter={"field":"amount","op":"gt","value":100}` and `filter={"field":"status","op":"eq","value":"OPEN"}`.
     * @param sort Optional repeated JSON sort clauses. Each `sort` value must be a JSON object with shape `{"field":"<column_name>","direction":"<direction>"}`. Allowed directions are: `asc`, `desc`. Repeat the query parameter to provide multi-column sorting in priority order. Example: `sort={"field":"created_at","direction":"desc"}`.
     * @param pageToken Opaque token from a previous response page.
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns RecordListResponse Successful Response
     * @throws ApiError
     */
    static recordList(podId, tableName, limit = 20, offset, filter, sort, pageToken, mode) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        query: {
          "limit": limit,
          "offset": offset,
          "filter": filter,
          "sort": sort,
          "page_token": pageToken,
          "mode": mode
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Record
     * Insert a record into a table. Returns the created record object keyed by column name (no envelope). Reserved tables (`reserved_*`) are system-managed and cannot be mutated through record write endpoints.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    static recordCreate(podId, tableName, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Bulk Create
     * Insert multiple records in one request. Returns the affected-row count.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns DatastoreCountResponse Successful Response
     * @throws ApiError
     */
    static recordBulkCreate(podId, tableName, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/create",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Bulk Delete
     * Delete multiple records by primary key values. Returns the affected-row count.
     * @param podId
     * @param tableName
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns DatastoreCountResponse Successful Response
     * @throws ApiError
     */
    static recordBulkDelete(podId, tableName, requestBody, mode) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/delete",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        query: {
          "mode": mode
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Bulk Update
     * Update multiple records in one request (each item needs primary key). Returns the affected-row count.
     * @param podId
     * @param tableName
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns DatastoreCountResponse Successful Response
     * @throws ApiError
     */
    static recordBulkUpdate(podId, tableName, requestBody, mode) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/update",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        query: {
          "mode": mode
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Record
     * Delete a record by primary key.
     * @param podId
     * @param tableName
     * @param recordId
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns void
     * @throws ApiError
     */
    static recordDelete(podId, tableName, recordId, mode) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}",
        path: {
          "pod_id": podId,
          "table_name": tableName,
          "record_id": recordId
        },
        query: {
          "mode": mode
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Record
     * Fetch one record by primary key value (returns the record object, no envelope). The `record_id` path segment is the table's primary key value as stored in the table, not necessarily a UUID.
     * @param podId
     * @param tableName
     * @param recordId
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns any Successful Response
     * @throws ApiError
     */
    static recordGet(podId, tableName, recordId, mode) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}",
        path: {
          "pod_id": podId,
          "table_name": tableName,
          "record_id": recordId
        },
        query: {
          "mode": mode
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Record
     * Patch a record by primary key. Returns the updated record object (no envelope).
     * @param podId
     * @param tableName
     * @param recordId
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires permission to administer the table; a caller without it gets a 403. Ignored for non-RLS tables, whose rows are shared by all members.
     * @returns any Successful Response
     * @throws ApiError
     */
    static recordUpdate(podId, tableName, recordId, requestBody, mode) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}",
        path: {
          "pod_id": podId,
          "table_name": tableName,
          "record_id": recordId
        },
        query: {
          "mode": mode
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/records.ts
  function serializeFilters(filters) {
    if (!filters || filters.length === 0) {
      return void 0;
    }
    return filters.map((filter) => JSON.stringify(filter));
  }
  function serializeSort(sort) {
    if (!sort || sort.length === 0) {
      return void 0;
    }
    return sort.map((entry) => JSON.stringify(entry));
  }
  var RecordsNamespace = class {
    constructor(client, podId) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
      __publicField(this, "bulk", {
        create: (table, records) => {
          const payload = { records };
          return this.client.request(() => RecordsService.recordBulkCreate(this.podId(), table, payload));
        },
        update: (table, records) => {
          const payload = { records };
          return this.client.request(() => RecordsService.recordBulkUpdate(this.podId(), table, payload));
        },
        delete: (table, recordIds) => {
          const payload = { record_ids: recordIds };
          return this.client.request(() => RecordsService.recordBulkDelete(this.podId(), table, payload));
        }
      });
    }
    list(table, options = {}) {
      const { filters, sort, limit, pageToken, offset } = options;
      return this.client.request(
        () => RecordsService.recordList(
          this.podId(),
          table,
          limit != null ? limit : 20,
          offset,
          serializeFilters(filters),
          serializeSort(sort),
          pageToken
        )
      );
    }
    create(table, data) {
      return this.client.request(() => RecordsService.recordCreate(this.podId(), table, { data }));
    }
    get(table, recordId) {
      return this.client.request(() => RecordsService.recordGet(this.podId(), table, recordId));
    }
    update(table, recordId, data) {
      return this.client.request(() => RecordsService.recordUpdate(this.podId(), table, recordId, { data }));
    }
    delete(table, recordId) {
      return this.client.request(() => RecordsService.recordDelete(this.podId(), table, recordId));
    }
    query(table, payload) {
      return this.client.request(() => {
        var _a;
        return RecordsService.recordList(
          this.podId(),
          table,
          (_a = payload.limit) != null ? _a : 20,
          payload.offset,
          serializeFilters(payload.filters),
          serializeSort(payload.sort),
          payload.page_token
        );
      });
    }
  };

  // src/openapi_client/services/PodResourceAccessService.ts
  var PodResourceAccessService = class {
    /**
     * Get Resource Access
     * @param podId
     * @param resourceType
     * @param resourceName
     * @returns ResourceAccessResponse Successful Response
     * @throws ApiError
     */
    static podResourceAccessGet(podId, resourceType, resourceName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/resources/{resource_type}/{resource_name}/access",
        path: {
          "pod_id": podId,
          "resource_type": resourceType,
          "resource_name": resourceName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Resource Access Grant
     * @param podId
     * @param resourceType
     * @param resourceName
     * @param granteeType
     * @param granteeId
     * @returns ResourceAccessResponse Successful Response
     * @throws ApiError
     */
    static podResourceAccessGrantDelete(podId, resourceType, resourceName, granteeType, granteeId) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/resources/{resource_type}/{resource_name}/access/grantees/{grantee_type}/{grantee_id}",
        path: {
          "pod_id": podId,
          "resource_type": resourceType,
          "resource_name": resourceName,
          "grantee_type": granteeType,
          "grantee_id": granteeId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Replace Resource Access Grant
     * @param podId
     * @param resourceType
     * @param resourceName
     * @param granteeType
     * @param granteeId
     * @param requestBody
     * @returns ResourceAccessResponse Successful Response
     * @throws ApiError
     */
    static podResourceAccessGrantReplace(podId, resourceType, resourceName, granteeType, granteeId, requestBody) {
      return request(OpenAPI, {
        method: "PUT",
        url: "/pods/{pod_id}/resources/{resource_type}/{resource_name}/access/grantees/{grantee_type}/{grantee_id}",
        path: {
          "pod_id": podId,
          "resource_type": resourceType,
          "resource_name": resourceName,
          "grantee_type": granteeType,
          "grantee_id": granteeId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/resource-access.ts
  var ResourceAccessNamespace = class {
    constructor(client, podId) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
    }
    get(resourceType, resourceName, podId) {
      return this.client.request(
        () => PodResourceAccessService.podResourceAccessGet(podId != null ? podId : this.podId(), resourceType, resourceName)
      );
    }
    replaceGrant(resourceType, resourceName, granteeType, granteeId, payload, podId) {
      return this.client.request(
        () => PodResourceAccessService.podResourceAccessGrantReplace(
          podId != null ? podId : this.podId(),
          resourceType,
          resourceName,
          granteeType,
          granteeId,
          payload
        )
      );
    }
    deleteGrant(resourceType, resourceName, granteeType, granteeId, podId) {
      return this.client.request(
        () => PodResourceAccessService.podResourceAccessGrantDelete(
          podId != null ? podId : this.podId(),
          resourceType,
          resourceName,
          granteeType,
          granteeId
        )
      );
    }
  };

  // src/openapi_client/services/SchedulesService.ts
  var SchedulesService = class {
    /**
     * List Schedules
     * List pod schedules.
     * @param podId
     * @param scheduleType
     * @param isActive
     * @param agentName
     * @param workflowName
     * @param name
     * @param limit
     * @param pageToken
     * @returns ScheduleListResponse Successful Response
     * @throws ApiError
     */
    static scheduleList(podId, scheduleType, isActive, agentName, workflowName, name, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/schedules",
        path: {
          "pod_id": podId
        },
        query: {
          "schedule_type": scheduleType,
          "is_active": isActive,
          "agent_name": agentName,
          "workflow_name": workflowName,
          "name": name,
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Schedule
     * Create a new pod schedule.
     * @param podId
     * @param requestBody
     * @returns ScheduleDetailResponse Successful Response
     * @throws ApiError
     */
    static scheduleCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/schedules",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Schedule
     * Delete a schedule.
     * @param podId
     * @param scheduleId
     * @returns void
     * @throws ApiError
     */
    static scheduleDelete(podId, scheduleId) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/schedules/{schedule_id}",
        path: {
          "pod_id": podId,
          "schedule_id": scheduleId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Schedule
     * Get a schedule by ID.
     * @param podId
     * @param scheduleId
     * @returns ScheduleDetailResponse Successful Response
     * @throws ApiError
     */
    static scheduleGet(podId, scheduleId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/schedules/{schedule_id}",
        path: {
          "pod_id": podId,
          "schedule_id": scheduleId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Schedule
     * Update a schedule.
     * @param podId
     * @param scheduleId
     * @param requestBody
     * @returns ScheduleDetailResponse Successful Response
     * @throws ApiError
     */
    static scheduleUpdate(podId, scheduleId, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/schedules/{schedule_id}",
        path: {
          "pod_id": podId,
          "schedule_id": scheduleId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/schedules.ts
  var SchedulesNamespace = class {
    constructor(client, podId) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
    }
    list(options = {}) {
      return this.client.request(
        () => {
          var _a;
          return SchedulesService.scheduleList(
            this.podId(),
            options.scheduleType,
            options.isActive,
            options.agentName,
            options.workflowName,
            options.name,
            (_a = options.limit) != null ? _a : 100,
            options.pageToken
          );
        }
      );
    }
    create(payload) {
      return this.client.request(() => SchedulesService.scheduleCreate(this.podId(), payload));
    }
    get(scheduleId) {
      return this.client.request(() => SchedulesService.scheduleGet(this.podId(), scheduleId));
    }
    update(scheduleId, payload) {
      return this.client.request(() => SchedulesService.scheduleUpdate(this.podId(), scheduleId, payload));
    }
    delete(scheduleId) {
      return this.client.request(() => SchedulesService.scheduleDelete(this.podId(), scheduleId));
    }
  };

  // src/openapi_client/services/TablesService.ts
  var TablesService = class {
    /**
     * List Tables
     * List tables in a datastore.
     * @param podId
     * @param limit Max number of tables to return.
     * @param pageToken Cursor from a previous response for pagination.
     * @returns TableListResponse Successful Response
     * @throws ApiError
     */
    static tableList(podId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/tables",
        path: {
          "pod_id": podId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Table
     * Create a table in a datastore. Define primary key, column schema, and optional RLS behavior.
     * @param podId
     * @param requestBody
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    static tableCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/tables",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Table
     * Delete a table and all records in it.
     * @param podId
     * @param tableName
     * @returns void
     * @throws ApiError
     */
    static tableDelete(podId, tableName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/datastore/tables/{table_name}",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Table
     * Get table schema metadata by table name.
     * @param podId
     * @param tableName
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    static tableGet(podId, tableName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/datastore/tables/{table_name}",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Table
     * Update table metadata/configuration, visibility, or toggle row-level security (enable_rls, empty tables only).
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    static tableUpdate(podId, tableName, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/datastore/tables/{table_name}",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Add Column
     * Add a new column to a table. Column names must be unique and compatible with existing table schema rules.
     * @param podId
     * @param tableName
     * @param requestBody
     * @returns TableDetailResponse Successful Response
     * @throws ApiError
     */
    static tableColumnAdd(podId, tableName, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/columns",
        path: {
          "pod_id": podId,
          "table_name": tableName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Remove Column
     * Remove a non-primary, non-system column from a table. System columns and the primary key cannot be removed.
     * @param podId
     * @param tableName
     * @param columnName
     * @returns void
     * @throws ApiError
     */
    static tableColumnRemove(podId, tableName, columnName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/datastore/tables/{table_name}/columns/{column_name}",
        path: {
          "pod_id": podId,
          "table_name": tableName,
          "column_name": columnName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/tables.ts
  function normalizeCreateTablePayload(payload) {
    if ("table_name" in payload) {
      const { table_name, name, ...rest } = payload;
      return {
        ...rest,
        name: name != null ? name : table_name
      };
    }
    return payload;
  }
  var TablesNamespace = class {
    constructor(client, podId) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
      __publicField(this, "columns", {
        add: (tableName, request2) => {
          const payload = "column" in request2 ? request2 : { column: request2 };
          return this.client.request(() => TablesService.tableColumnAdd(this.podId(), tableName, payload));
        },
        remove: (tableName, columnName) => this.client.request(() => TablesService.tableColumnRemove(this.podId(), tableName, columnName))
      });
    }
    list(options = {}) {
      return this.client.request(() => {
        var _a;
        return TablesService.tableList(this.podId(), (_a = options.limit) != null ? _a : 100, options.pageToken);
      });
    }
    create(payload) {
      return this.client.request(() => TablesService.tableCreate(this.podId(), normalizeCreateTablePayload(payload)));
    }
    get(tableName) {
      return this.client.request(() => TablesService.tableGet(this.podId(), tableName));
    }
    update(tableName, payload) {
      return this.client.request(() => TablesService.tableUpdate(this.podId(), tableName, payload));
    }
    delete(tableName) {
      return this.client.request(() => TablesService.tableDelete(this.podId(), tableName));
    }
  };

  // src/openapi_client/services/UsersService.ts
  var UsersService = class {
    /**
     * Get Current User
     * Get the current authenticated user's information
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    static userCurrentGet() {
      return request(OpenAPI, {
        method: "GET",
        url: "/users/me"
      });
    }
    /**
     * Get User Profile
     * Get the current user's profile
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    static userProfileGet() {
      return request(OpenAPI, {
        method: "GET",
        url: "/users/me/profile"
      });
    }
    /**
     * Create or Update Profile
     * Create or update the current user's profile
     * @param requestBody
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    static userProfileUpsert(requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/users/me/profile",
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/users.ts
  var UsersNamespace = class {
    constructor(client) {
      __publicField(this, "client", client);
    }
    current() {
      return this.client.request(() => UsersService.userCurrentGet());
    }
    getProfile() {
      return this.client.request(() => UsersService.userProfileGet());
    }
    upsertProfile(payload) {
      return this.client.request(() => UsersService.userProfileUpsert(payload));
    }
  };

  // src/openapi_client/services/WorkflowsService.ts
  var WorkflowsService = class {
    /**
     * List Workflow Runs Waiting For Current User
     * The current user's approval queue: active form waits assigned to them, with the owning run.
     * @param podId
     * @param limit
     * @param pageToken
     * @returns WorkflowRunWaitAssignmentListResponse Successful Response
     * @throws ApiError
     */
    static workflowRunWaitingAssignedToMe(podId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/workflow-runs/waiting/assigned-to-me",
        path: {
          "pod_id": podId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Workflow Run
     * Get current state, context, step history, and the active wait (when WAITING) of a workflow run.
     * @param podId
     * @param runId
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    static workflowRunGet(podId, runId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/workflow-runs/{run_id}",
        path: {
          "pod_id": podId,
          "run_id": runId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Cancel Workflow Run
     * Cancel a non-terminal run. The active wait (if any) is cancelled in the same transaction; late completion events for cancelled waits are dropped. Cancelling a terminal run returns 409.
     * @param podId
     * @param runId
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    static workflowRunCancel(podId, runId) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/workflow-runs/{run_id}/cancel",
        path: {
          "pod_id": podId,
          "run_id": runId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Submit Workflow Run Form
     * Submit the form the run is waiting on. `node_id` must match the run's active HUMAN wait (409 when the run is not waiting on a form, 422 on node mismatch, 403 when the wait is assigned to someone else). The submitted `inputs` become the form node's output, available to later nodes as `<node_id>.<field>`.
     * @param podId
     * @param runId
     * @param requestBody
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    static workflowRunFormSubmit(podId, runId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/workflow-runs/{run_id}/form",
        path: {
          "pod_id": podId,
          "run_id": runId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Visualize Workflow Run
     * Render an HTML view of a run overlaid on its workflow graph.
     * @param podId
     * @param runId
     * @returns string Successful Response
     * @throws ApiError
     */
    static workflowRunVisualize(podId, runId) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/workflow-runs/{run_id}/visualize",
        path: {
          "pod_id": podId,
          "run_id": runId
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Workflows
     * List all workflows in a pod.
     * @param podId
     * @param limit
     * @param pageToken
     * @returns WorkflowListResponse Successful Response
     * @throws ApiError
     */
    static workflowList(podId, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/workflows",
        path: {
          "pod_id": podId
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Workflow
     * Create a workflow definition. The graph (`nodes`/`edges`) can be included in this call to create a ready-to-run workflow in one step, or omitted to create a shell and upload the graph later with `workflow.graph.update`.
     * @param podId
     * @param requestBody
     * @returns FlowDetailResponse Successful Response
     * @throws ApiError
     */
    static workflowCreate(podId, requestBody) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/workflows",
        path: {
          "pod_id": podId
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Delete Workflow
     * Delete a workflow definition.
     * @param podId
     * @param workflowName
     * @returns void
     * @throws ApiError
     */
    static workflowDelete(podId, workflowName) {
      return request(OpenAPI, {
        method: "DELETE",
        url: "/pods/{pod_id}/workflows/{workflow_name}",
        path: {
          "pod_id": podId,
          "workflow_name": workflowName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Get Workflow
     * Get a single workflow definition including graph and start configuration.
     * @param podId
     * @param workflowName
     * @returns FlowDetailResponse Successful Response
     * @throws ApiError
     */
    static workflowGet(podId, workflowName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/workflows/{workflow_name}",
        path: {
          "pod_id": podId,
          "workflow_name": workflowName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Workflow Metadata
     * Update workflow-level metadata such as description and schedule mode. Workflow names are immutable after creation. Use `workflow.graph.update` for nodes and edges.
     * @param podId
     * @param workflowName
     * @param requestBody
     * @returns FlowDetailResponse Successful Response
     * @throws ApiError
     */
    static workflowUpdate(podId, workflowName, requestBody) {
      return request(OpenAPI, {
        method: "PATCH",
        url: "/pods/{pod_id}/workflows/{workflow_name}",
        path: {
          "pod_id": podId,
          "workflow_name": workflowName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Update Workflow Graph
     * Replace the workflow graph. Agent/function node `input_mapping` entries must use explicit typed bindings. Use `{type: "expression", value: "start.payload.issue.key"}` for context lookups and `{type: "literal", value: "abc"}` for fixed JSON values.
     * @param podId
     * @param workflowName
     * @param requestBody
     * @returns FlowDetailResponse Successful Response
     * @throws ApiError
     */
    static workflowGraphUpdate(podId, workflowName, requestBody) {
      return request(OpenAPI, {
        method: "PUT",
        url: "/pods/{pod_id}/workflows/{workflow_name}/graph",
        path: {
          "pod_id": podId,
          "workflow_name": workflowName
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * List Workflow Runs
     * List recent runs for a given workflow.
     * @param podId
     * @param workflowName
     * @param limit
     * @param pageToken
     * @returns WorkflowRunListResponse Successful Response
     * @throws ApiError
     */
    static workflowRunList(podId, workflowName, limit = 100, pageToken) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/workflows/{workflow_name}/runs",
        path: {
          "pod_id": podId,
          "workflow_name": workflowName
        },
        query: {
          "limit": limit,
          "page_token": pageToken
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Create Workflow Run
     * Create a new run for this workflow. Takes no request body: if the workflow's entry node is a FORM node the run is created WAITING on it (see `active_wait` in the response) and input is submitted via `workflow.run.form.submit`; otherwise the run executes immediately. Trigger payloads for scheduled/event/datastore starts are supplied by the platform, not through this endpoint.
     * @param podId
     * @param workflowName
     * @returns WorkflowRunResponse Successful Response
     * @throws ApiError
     */
    static workflowRunCreate(podId, workflowName) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/workflows/{workflow_name}/runs",
        path: {
          "pod_id": podId,
          "workflow_name": workflowName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
    /**
     * Visualize Workflow
     * Render an HTML visualization for debugging workflow graph structure.
     * @param podId
     * @param workflowName
     * @returns string Successful Response
     * @throws ApiError
     */
    static workflowVisualize(podId, workflowName) {
      return request(OpenAPI, {
        method: "GET",
        url: "/pods/{pod_id}/workflows/{workflow_name}/visualize",
        path: {
          "pod_id": podId,
          "workflow_name": workflowName
        },
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/namespaces/workflows.ts
  var WorkflowsNamespace = class {
    constructor(client, http, podId) {
      __publicField(this, "client", client);
      __publicField(this, "http", http);
      __publicField(this, "podId", podId);
      __publicField(this, "graph", {
        update: (workflowName, graph) => this.client.request(() => WorkflowsService.workflowGraphUpdate(this.podId(), workflowName, graph))
      });
      __publicField(this, "runs", {
        /**
         * Create a run. Runs take no inputs: when the workflow starts with a
         * form, the returned run is WAITING with `active_wait` describing the
         * form to render and submit via `runs.submitForm`.
         */
        create: (workflowName) => this.client.request(() => WorkflowsService.workflowRunCreate(this.podId(), workflowName)),
        list: (workflowName, options = {}) => this.client.request(
          () => {
            var _a;
            return WorkflowsService.workflowRunList(this.podId(), workflowName, (_a = options.limit) != null ? _a : 100, options.pageToken);
          }
        ),
        waitingAssignedToMe: (options = {}) => this.client.request(
          () => {
            var _a;
            return WorkflowsService.workflowRunWaitingAssignedToMe(this.podId(), (_a = options.limit) != null ? _a : 100, options.pageToken);
          }
        ),
        get: (runId, podId = this.podId()) => this.client.request(() => WorkflowsService.workflowRunGet(podId, runId)),
        /** Submit the form a run is waiting on. node_id must match active_wait.node_id. */
        submitForm: (runId, payload, podId = this.podId()) => this.client.request(() => WorkflowsService.workflowRunFormSubmit(podId, runId, payload)),
        visualize: (runId, podId = this.podId()) => this.client.request(() => WorkflowsService.workflowRunVisualize(podId, runId)),
        cancel: (runId, podId = this.podId()) => this.client.request(() => WorkflowsService.workflowRunCancel(podId, runId))
      });
    }
    list(options = {}) {
      return this.client.request(() => {
        var _a;
        return WorkflowsService.workflowList(this.podId(), (_a = options.limit) != null ? _a : 100, options.pageToken);
      });
    }
    create(payload) {
      return this.client.request(() => WorkflowsService.workflowCreate(this.podId(), payload));
    }
    get(workflowName) {
      return this.client.request(() => WorkflowsService.workflowGet(this.podId(), workflowName));
    }
    update(workflowName, payload) {
      return this.client.request(() => WorkflowsService.workflowUpdate(this.podId(), workflowName, payload));
    }
    delete(workflowName) {
      return this.client.request(() => WorkflowsService.workflowDelete(this.podId(), workflowName));
    }
    visualize(workflowName) {
      return this.client.request(() => WorkflowsService.workflowVisualize(this.podId(), workflowName));
    }
  };

  // src/namespaces/widgets.ts
  var WidgetsNamespace = class {
    constructor(http, podId) {
      __publicField(this, "http", http);
      __publicField(this, "podId", podId);
    }
    /**
     * Mint a short-lived, signed embed URL for a conversation widget. The widget
     * serve route is authenticated; this returns a URL the iframe can load even
     * when the session cookie is not sent in a cross-site/embedded context.
     */
    embedUrl(payload) {
      return this.http.request(
        "POST",
        `/pods/${this.podId()}/widgets/${payload.conversation_id}/${payload.tool_call_id}/embed-token`
      );
    }
  };

  // src/openapi_client/services/QueryService.ts
  var QueryService = class {
    /**
     * Execute Query
     * Execute a read-only SQL query inside the datastore schema. Joins, aggregates, subqueries, and cross-table reads are allowed, including across RLS-enabled tables — rows of RLS tables are scoped to the caller by default (pod admins included). Pass `mode=admin` to read every member's rows, which requires permission to administer each referenced RLS table. Only a single read-only statement is permitted; mutating statements and cross-schema references are rejected.
     * @param podId
     * @param requestBody
     * @param mode Row-visibility mode for RLS-enabled tables referenced by the query. Omitted/`USER` (default) scopes their rows to the signed-in user — the per-user data apps and functions expect. `ADMIN` returns every member's rows and requires permission to administer every RLS table the query touches; a caller without it gets a 403. Non-RLS tables are unaffected.
     * @returns DatastoreQueryResponse Successful Response
     * @throws ApiError
     */
    static queryExecute(podId, requestBody, mode) {
      return request(OpenAPI, {
        method: "POST",
        url: "/pods/{pod_id}/datastore/query",
        path: {
          "pod_id": podId
        },
        query: {
          "mode": mode
        },
        body: requestBody,
        mediaType: "application/json",
        errors: {
          422: `Validation Error`
        }
      });
    }
  };

  // src/datastore-changes.ts
  var RECONNECT_BASE_DELAY_MS = 500;
  var RECONNECT_MAX_DELAY_MS = 3e4;
  var WS_POLICY_VIOLATION = 1008;
  function reconnectDelayMs(attempt) {
    const ceiling = Math.min(
      RECONNECT_MAX_DELAY_MS,
      RECONNECT_BASE_DELAY_MS * 2 ** Math.max(0, attempt)
    );
    return Math.random() * ceiling;
  }
  function changesWsUrl(apiUrl, podId, table, since, token) {
    const root = apiUrl.replace(/\/$/, "").replace(/^http(s?):\/\//, "ws$1://");
    const url = new URL(`${root}/pods/${podId}/datastore/changes`);
    if (table) url.searchParams.set("table", table);
    if (since) url.searchParams.set("since", since);
    if (token) url.searchParams.set("access_token", token);
    return url.toString();
  }
  function watchDatastoreChanges(apiUrl, auth, podId, options) {
    let socket = null;
    let cursor = options.since;
    let attempt = 0;
    let stopped = false;
    let reconnectTimer = null;
    const status = (next) => {
      var _a;
      return (_a = options.onStatus) == null ? void 0 : _a.call(options, next);
    };
    const scheduleReconnect = () => {
      var _a;
      if (stopped) return;
      if (options.maxRetries != null && attempt >= options.maxRetries) {
        stopped = true;
        status("closed");
        (_a = options.onError) == null ? void 0 : _a.call(
          options,
          new Error("Datastore change stream: max reconnect attempts reached")
        );
        return;
      }
      const delay = reconnectDelayMs(attempt);
      attempt += 1;
      status("reconnecting");
      reconnectTimer = setTimeout(() => void connect(), delay);
    };
    const connect = async () => {
      var _a;
      if (stopped) return;
      status(attempt === 0 ? "connecting" : "reconnecting");
      let token = null;
      if (!options.useCookie) {
        try {
          token = await auth.getAccessToken();
        } catch {
          token = null;
        }
      }
      if (stopped) return;
      let ws;
      try {
        ws = new WebSocket(changesWsUrl(apiUrl, podId, options.table, cursor, token));
      } catch (error) {
        (_a = options.onError) == null ? void 0 : _a.call(options, error instanceof Error ? error : new Error(String(error)));
        scheduleReconnect();
        return;
      }
      socket = ws;
      ws.onopen = () => {
        attempt = 0;
        status("open");
      };
      ws.onmessage = (event) => {
        var _a2;
        let frame;
        try {
          frame = JSON.parse(typeof event.data === "string" ? event.data : "");
        } catch {
          return;
        }
        if (!frame || typeof frame !== "object") return;
        const record = frame;
        if (record.type === "ready") {
          cursor = record.since || cursor;
          if (cursor) (_a2 = options.onReady) == null ? void 0 : _a2.call(options, { since: cursor });
          return;
        }
        cursor = record.stream_id || cursor;
        options.onChange(record);
      };
      ws.onclose = (event) => {
        socket = null;
        if (stopped) {
          status("closed");
          return;
        }
        if (event.code === WS_POLICY_VIOLATION && !options.useCookie) {
          auth.refreshAccessToken().then(scheduleReconnect, scheduleReconnect);
          return;
        }
        scheduleReconnect();
      };
      ws.onerror = () => {
      };
    };
    const close = () => {
      if (stopped) return;
      stopped = true;
      if (reconnectTimer != null) clearTimeout(reconnectTimer);
      if (socket) {
        try {
          socket.close(1e3, "client closed");
        } catch {
        }
        socket = null;
      }
      status("closed");
    };
    if (options.signal) {
      if (options.signal.aborted) stopped = true;
      else options.signal.addEventListener("abort", close, { once: true });
    }
    if (!stopped) void connect();
    return {
      close,
      get closed() {
        return stopped;
      }
    };
  }

  // src/namespaces/datastore.ts
  var DatastoreNamespace = class {
    constructor(client, podId, apiUrl, auth) {
      __publicField(this, "client", client);
      __publicField(this, "podId", podId);
      __publicField(this, "apiUrl", apiUrl);
      __publicField(this, "auth", auth);
    }
    query(request2) {
      const payload = typeof request2 === "string" ? { query: request2 } : request2;
      return this.client.request(() => QueryService.queryExecute(this.podId(), payload));
    }
    /**
     * Stream live record changes (insert/update/delete) over a WebSocket.
     *
     * Returns a handle; call `handle.close()` (or abort `options.signal`) to stop.
     * RLS tables deliver only the caller's own rows; shared tables deliver all
     * members' changes. Reconnects automatically and resumes from the last change.
     */
    watchChanges(options) {
      return watchDatastoreChanges(this.apiUrl, this.auth, this.podId(), options);
    }
  };

  // src/client.ts
  var LemmaClient = class _LemmaClient {
    constructor(overrides = {}, internalOptions = {}) {
      __publicField(this, "_config");
      __publicField(this, "_podId");
      __publicField(this, "_currentPodId");
      /** Auth manager — subscribe to auth state, check auth, redirect to auth. */
      __publicField(this, "auth");
      __publicField(this, "_http");
      __publicField(this, "_generated");
      // Namespaces
      __publicField(this, "tables");
      __publicField(this, "records");
      __publicField(this, "files");
      __publicField(this, "functions");
      __publicField(this, "agents");
      __publicField(this, "agentRuntime");
      __publicField(this, "conversations");
      __publicField(this, "workflows");
      __publicField(this, "apps");
      __publicField(this, "widgets");
      __publicField(this, "connectors");
      __publicField(this, "resourceAccess");
      __publicField(this, "schedules");
      __publicField(this, "datastore");
      /** Alias of {@link datastore}, matching the Python SDK's `pod.queries`. */
      __publicField(this, "queries");
      __publicField(this, "users");
      __publicField(this, "icons");
      __publicField(this, "pods");
      __publicField(this, "podMembers");
      __publicField(this, "podPermissions");
      __publicField(this, "podJoinRequests");
      __publicField(this, "podRoles");
      __publicField(this, "organizations");
      __publicField(this, "podSurfaces");
      var _a;
      this._config = resolveConfig(overrides);
      this._currentPodId = this._config.podId;
      this._podId = this._config.podId;
      this.auth = (_a = internalOptions.authManager) != null ? _a : new AuthManager(this._config.apiUrl, this._config.authUrl);
      this._http = new HttpClient(this._config.apiUrl, this.auth, {
        timeoutMs: this._config.timeoutMs,
        maxRetries: this._config.maxRetries
      });
      this._generated = new GeneratedClientAdapter(this._config.apiUrl, this.auth, {
        maxRetries: this._config.maxRetries,
        timeoutMs: this._config.timeoutMs
      });
      const podIdFn = () => {
        if (!this._currentPodId) {
          throw new Error(
            "pod_id is required. Pass podId in the constructor or call client.setPodId(id)."
          );
        }
        return this._currentPodId;
      };
      this.tables = new TablesNamespace(this._generated, podIdFn);
      this.records = new RecordsNamespace(this._generated, podIdFn);
      this.files = new FilesNamespace(this._generated, this._http, podIdFn);
      this.functions = new FunctionsNamespace(this._generated, podIdFn);
      this.agents = new AgentsNamespace(this._generated, podIdFn, () => this.conversations);
      this.agentRuntime = new AgentRuntimeNamespace(this._generated);
      this.conversations = new ConversationsNamespace(this._http, podIdFn);
      this.workflows = new WorkflowsNamespace(this._generated, this._http, podIdFn);
      this.apps = new AppsNamespace(this._generated, this._http, podIdFn);
      this.widgets = new WidgetsNamespace(this._http, podIdFn);
      this.connectors = new ConnectorsNamespace(this._generated, this._http);
      this.resourceAccess = new ResourceAccessNamespace(this._generated, podIdFn);
      this.schedules = new SchedulesNamespace(this._generated, podIdFn);
      this.datastore = new DatastoreNamespace(
        this._generated,
        podIdFn,
        this._config.apiUrl,
        this.auth
      );
      this.queries = this.datastore;
      this.users = new UsersNamespace(this._generated);
      this.icons = new IconsNamespace(this._generated);
      this.pods = new PodsNamespace(this._generated, this._http);
      this.podMembers = new PodMembersNamespace(this._generated);
      this.podPermissions = new PodPermissionsNamespace(this._generated, this._http, podIdFn);
      this.podJoinRequests = new PodJoinRequestsNamespace(this._generated);
      this.podRoles = new PodRolesNamespace(this._generated, podIdFn);
      this.organizations = new OrganizationsNamespace(this._generated, this._http);
      this.podSurfaces = new PodSurfacesNamespace(this._generated);
    }
    /** Change the active pod ID for subsequent calls. */
    setPodId(podId) {
      this._currentPodId = podId;
    }
    /** Return a new client scoped to a specific pod, sharing auth state. */
    withPod(podId) {
      return new _LemmaClient({ ...this._config, podId }, { authManager: this.auth });
    }
    get podId() {
      return this._currentPodId;
    }
    get apiUrl() {
      return this._config.apiUrl;
    }
    get authUrl() {
      return this._config.authUrl;
    }
    /**
     * Initialize the client by checking auth state.
     * Call this once on app startup (or let AuthGuard handle it).
     */
    async initialize() {
      return this.auth.checkAuth();
    }
    /** Raw HTTP request — escape hatch for operations not covered by namespaces. */
    request(method, path, options) {
      return this._http.request(method, path, options);
    }
  };

  // src/browser.ts
  if (typeof globalThis !== "undefined") {
    const scope = globalThis;
    const surface = {
      LemmaClient,
      AuthManager,
      buildAuthUrl,
      buildFederatedLogoutUrl,
      clearTestingToken,
      getTestingToken,
      resolveSafeRedirectUri,
      setTestingToken,
      ApiError
    };
    if (!scope.LemmaClient) {
      scope.LemmaClient = surface;
    }
    if (!scope.Lemma) {
      scope.Lemma = surface;
    }
  }
  return __toCommonJS(browser_exports);
})();
