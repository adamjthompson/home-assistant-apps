const MazdaAuthClient = require('./MazdaAuthClient');
const c = new MazdaAuthClient("email@adamjthompson.com", "!8G3Uvrtt*c4");
c.getAccessToken().then(t => console.log(t)).catch(console.error);